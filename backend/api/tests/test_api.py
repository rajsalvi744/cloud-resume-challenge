# tests/test_backend_emulator.py
import os
import uuid
import logging
import time
from dotenv import load_dotenv
import pytest
from azure.cosmos import CosmosClient, exceptions

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from function_app import (
    CosmosConfig,
    VisitorCounterService,
    setup_logging,
)


load_dotenv()

# Cosmos DB Emulator Configuration
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT", "https://localhost:8081")
COSMOS_KEY = os.getenv("COSMOS_KEY", "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==")
DATABASE_NAME = os.getenv("COSMOS_TEST_DATABASE")
CONTAINER_NAME = os.getenv("COSMOS_TEST_CONTAINER")
PARTITION_KEY_PATH = os.getenv("COSMOS_TEST_PARTITION_KEY_PATH")  # Ensure this starts with a '/'
PARTITION_KEY = os.getenv("COSMOS_TEST_PARTITION_KEY")
COUNTER_ID = os.getenv("COSMOS_TEST_COUNTER_ID")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@pytest.fixture(scope="module")
def cosmos_client():
    """Fixture to create a Cosmos DB client for the emulator."""
    client = CosmosClient(COSMOS_ENDPOINT,
                          COSMOS_KEY,
                          connection_verify=False)
    time.sleep(5)
    yield client
    # # Cleanup: Delete the test database
    # try:
    #     client.delete_database(DATABASE_NAME)
    #     logger.info(f"Deleted database: {DATABASE_NAME}")
    # except exceptions.CosmosResourceNotFoundError:
    #     logger.warning(f"Database not found: {DATABASE_NAME}")

@pytest.fixture(scope="module")
def test_database(cosmos_client):
    """Fixture to create a test database."""
    database = cosmos_client.create_database_if_not_exists(id=DATABASE_NAME)
    logger.info(f"Created database: {DATABASE_NAME}")
    time.sleep(5)
    yield database
    # Cleanup is handled by the cosmos_client fixture

@pytest.fixture(scope="module")
def test_container(test_database):
    """Fixture to create a test container."""
    container = test_database.create_container_if_not_exists(
        id=CONTAINER_NAME,
        partition_key=PARTITION_KEY_PATH
    )
    time.sleep(5)
    logger.info(f"Created container: {CONTAINER_NAME}")
    yield container
    # Cleanup is handled by the test_database fixture

@pytest.fixture
def cosmos_config():
    """Fixture to create a CosmosConfig object for testing."""
    return CosmosConfig(
        endpoint=COSMOS_ENDPOINT,
        key=COSMOS_KEY,
        database_name=DATABASE_NAME,
        container_name=CONTAINER_NAME,
        partition_key=PARTITION_KEY,
        counter_id=COUNTER_ID,
    )

@pytest.fixture
def visitor_counter_service(cosmos_config):
    """Fixture to create a VisitorCounterService instance."""
    logger = setup_logging()
    return VisitorCounterService(cosmos_config, logger)

@pytest.mark.asyncio
async def test_increment_counter(visitor_counter_service, test_container):
    """Test that the visitor counter increments correctly."""
    # Initial increment
    count, status = await visitor_counter_service.increment_counter(str(uuid.uuid4()))
    assert status == 201  # 201 Created
    assert count == 1

    # Verify the count in the database
    item = test_container.read_item(item=COUNTER_ID, partition_key=PARTITION_KEY)
    assert item["count"] == 1

    # Second increment
    count, status = await visitor_counter_service.increment_counter(str(uuid.uuid4()))
    assert status == 200  # 200 OK
    assert count == 2

    # Verify the count in the database
    item = test_container.read_item(item=COUNTER_ID, partition_key=PARTITION_KEY)
    assert item["count"] == 2

@pytest.mark.asyncio
async def test_get_counter(visitor_counter_service, test_container):
    """Test that the visitor counter is retrieved correctly."""
    # Set up initial count
    test_container.upsert_item({
        "id": COUNTER_ID,
        "count": 5,
        "visitorCount": PARTITION_KEY
    })
    logger.info("Initial count set to 5")

    # Retrieve the count
    count, status = await visitor_counter_service.get_counter(str(uuid.uuid4()))
    assert status == 200
    assert count == 5

@pytest.mark.asyncio
async def test_counter_initialization(visitor_counter_service, test_container):
    """Test that the counter is initialized if it doesn't exist."""
    # Delete the counter if it exists
    try:
        test_container.delete_item(item=COUNTER_ID, partition_key=PARTITION_KEY)
        logger.info("Deleted existing counter")
    except exceptions.CosmosResourceNotFoundError:
        logger.info("Counter does not exist, skipping deletion")

    # Increment the counter
    count, status = await visitor_counter_service.increment_counter(str(uuid.uuid4()))
    assert status == 201  # 201 Created
    assert count == 1

    # Verify the count in the database
    item = test_container.read_item(item=COUNTER_ID, partition_key=PARTITION_KEY)
    assert item["count"] == 1
