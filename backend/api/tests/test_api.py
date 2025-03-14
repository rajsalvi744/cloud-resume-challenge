# tests/test_api.py
import os
import json
import pytest
import logging
from unittest.mock import MagicMock
import azure.functions as func
from azure.cosmos import CosmosClient, exceptions
# Import the module to test
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from function_app import (
    CosmosConfig,
    VisitorCounterService,
    generate_correlation_id,
    main,
    MAX_RETRIES,
    setup_logging
)

# Constants for test environment
TEST_DATABASE = "TestDatabase"
TEST_CONTAINER = "TestContainer"
ENV_FILE = ".env"

@pytest.fixture(scope="session", autouse=True)
def load_env():
    """Load environment variables from .env.test file if exists"""
    if os.path.exists(ENV_FILE):
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=True)

@pytest.fixture(scope="module", autouse=True)
def setup_test_infrastructure():
    """Set up test database and container once per test session"""
    config = CosmosConfig.from_env()
    client = CosmosClient(config.endpoint, config.key)

    # Create test database
    database = client.create_database_if_not_exists(TEST_DATABASE)

    # Create test container
    database.create_container_if_not_exists(
        id=TEST_CONTAINER,
        partition_key=config.partition_key
    )

    yield  # Test session teardown

    # Cleanup only if CI environment
    if os.getenv("CI") == "true":
        try:
            client.delete_database(TEST_DATABASE)
        except exceptions.CosmosResourceNotFoundError:
            pass

@pytest.fixture(autouse=True)
def mock_logging(monkeypatch):
    """Mock logging setup for tests"""
    monkeypatch.setattr("function_app.setup_logging", lambda: logging.getLogger(__name__))

@pytest.fixture
def test_config(monkeypatch):
    """Test configuration with environment overrides"""
    monkeypatch.setenv("COSMOS_DATABASE", TEST_DATABASE)
    monkeypatch.setenv("COSMOS_CONTAINER", TEST_CONTAINER)
    return CosmosConfig.from_env()

@pytest.fixture
def test_service(test_config):
    """Service instance with test configuration"""
    return VisitorCounterService(test_config, logging.getLogger())

@pytest.fixture(autouse=True)
def clear_test_data(test_config):
    """Clean test data before each test"""
    client = CosmosClient(test_config.endpoint, test_config.key)
    container = client.get_database_client(test_config.database_name)\
                     .get_container_client(test_config.container_name)

    try:
        container.delete_item(
            item=test_config.counter_id,
            partition_key=test_config.partition_key
        )
    except exceptions.CosmosResourceNotFoundError:
        pass

# Unit Tests
def test_cosmos_config_validation():
    with pytest.raises(ValueError):
        CosmosConfig(
            endpoint="invalid_url",
            key="valid_key",
            database_name="test",
            container_name="test",
            partition_key="test",
            counter_id="test"
        )

def test_correlation_id_generation():
    req = func.HttpRequest("GET", "/", body=None)
    corr_id = generate_correlation_id(req)
    assert len(corr_id) == 64

# Integration Tests
@pytest.mark.asyncio
async def test_get_counter_initial(test_service):
    count, status = await test_service.get_counter("test")
    assert status == 200
    assert count == 0

@pytest.mark.asyncio
async def test_increment_counter(test_service):
    count, status = test_service.increment_counter("test")
    assert status == 201
    assert count == 1

# HTTP Endpoint Tests
@pytest.mark.asyncio
async def test_http_get():
    req = func.HttpRequest("GET", "/counter", body=None)
    response = await main(req)
    assert response.status_code == 200
    assert json.loads(response.get_body())["count"] >= 0

@pytest.mark.asyncio
async def test_http_post():
    req = func.HttpRequest("POST", "/counter", body=None)
    response = await main(req)
    assert response.status_code in (200, 201)
    data = json.loads(response.get_body())
    assert isinstance(data["count"], int)

# Error Handling Tests
@pytest.mark.asyncio
async def test_database_unavailable(test_service, mocker):
    # Create a proper CosmosHttpResponseError with status code
    mock_error = exceptions.CosmosHttpResponseError(
        status_code=500,  # <-- Add numeric status code
        message="Simulated database error"
    )

    mocker.patch.object(
        test_service.container,
        "read_item",
        side_effect=mock_error
    )

    count, status = await test_service.get_counter("test")
    assert status == 500
