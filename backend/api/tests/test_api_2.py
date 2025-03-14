import os
import unittest
import json
import hashlib
from unittest import mock
from datetime import datetime, timezone
import logging

import azure.functions as func
from azure.cosmos import exceptions

# Import the module to test
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from function_app import (
    VisitorCounterService,
    CosmosConfig,
    generate_correlation_id,
    main,
    setup_logging,
    MAX_RETRIES
)

class TestCosmosConfig(unittest.TestCase):
    """Tests for the CosmosConfig class"""

    def test_valid_config(self):
        """Test valid configuration parameters"""
        config = CosmosConfig(
            endpoint="https://example.cosmos.azure.com",
            key="valid-key",
            database_name="TestDB",
            container_name="TestContainer",
            partition_key="TestPartition",
            counter_id="TestCounter"
        )
        self.assertEqual(config.endpoint, "https://example.cosmos.azure.com")
        self.assertEqual(config.key, "valid-key")
        self.assertEqual(config.database_name, "TestDB")
        self.assertEqual(config.container_name, "TestContainer")
        self.assertEqual(config.partition_key, "TestPartition")
        self.assertEqual(config.counter_id, "TestCounter")

    def test_invalid_endpoint(self):
        """Test invalid endpoint format"""
        with self.assertRaises(ValueError):
            CosmosConfig(
                endpoint="invalid-endpoint",
                key="valid-key",
                database_name="TestDB",
                container_name="TestContainer",
                partition_key="TestPartition",
                counter_id="TestCounter"
            )

    def test_empty_key(self):
        """Test empty key validation"""
        with self.assertRaises(ValueError):
            CosmosConfig(
                endpoint="https://example.cosmos.azure.com",
                key="",
                database_name="TestDB",
                container_name="TestContainer",
                partition_key="TestPartition",
                counter_id="TestCounter"
            )

    def test_empty_database_params(self):
        """Test empty database parameters validation"""
        with self.assertRaises(ValueError):
            CosmosConfig(
                endpoint="https://example.cosmos.azure.com",
                key="valid-key",
                database_name="",
                container_name="TestContainer",
                partition_key="TestPartition",
                counter_id="TestCounter"
            )

    @mock.patch.dict(os.environ, {
        "COSMOS_ENDPOINT": "https://example.cosmos.azure.com",
        "COSMOS_KEY": "valid-key"
    })
    def test_from_env_with_defaults(self):
        """Test creation from environment variables with defaults"""
        config = CosmosConfig.from_env()
        self.assertEqual(config.endpoint, "https://example.cosmos.azure.com")
        self.assertEqual(config.key, "valid-key")
        self.assertEqual(config.database_name, "TestDatabase")
        self.assertEqual(config.container_name, "TestContainer")
        self.assertEqual(config.partition_key, "visitorCount")
        self.assertEqual(config.counter_id, "visitorCount")

    @mock.patch.dict(os.environ, {
        "COSMOS_ENDPOINT": "https://example.cosmos.azure.com",
        "COSMOS_KEY": "valid-key",
        "COSMOS_DATABASE": "TestDB",
        "COSMOS_CONTAINER": "TestContainer",
        "COSMOS_PARTITION_KEY": "TestPartition",
        "COSMOS_COUNTER_ID": "TestCounter"
    })
    def test_from_env_custom_values(self):
        """Test creation from custom environment variables"""
        config = CosmosConfig.from_env()
        self.assertEqual(config.endpoint, "https://example.cosmos.azure.com")
        self.assertEqual(config.key, "valid-key")
        self.assertEqual(config.database_name, "TestDB")
        self.assertEqual(config.container_name, "TestContainer")
        self.assertEqual(config.partition_key, "TestPartition")
        self.assertEqual(config.counter_id, "TestCounter")

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_from_env_missing_vars(self):
        print(os.environ)
        """Test error when required environment variables are missing"""
        with self.assertRaises(ValueError):
            CosmosConfig.from_env()


class TestVisitorCounterService(unittest.TestCase):
    """Tests for the VisitorCounterService class"""

    def setUp(self):

        # Set up mocks FIRST, before creating the service
        # Patch the cosmos_client from the function_app module
        self.cosmos_client_patcher = mock.patch('function_app.CosmosClient')
        self.mock_cosmos_client = self.cosmos_client_patcher.start()

        # Setup mock chain: client -> database -> container
        self.mock_container = mock.MagicMock()
        self.mock_database = mock.MagicMock()
        self.mock_database.get_container_client.return_value = self.mock_container
        self.mock_cosmos_client.return_value.get_database_client.return_value = self.mock_database

        self.config = CosmosConfig(
            endpoint="https://example.cosmos.azure.com",
            key="valid-key",
            database_name="TestDB",
            container_name="TestContainer",
            partition_key="TestPartition",
            counter_id="TestCounter"
        )
        self.logger = logging.getLogger(__name__)
        self.correlation_id = "a" * 64  # Valid correlation ID

    def tearDown(self):
        self.cosmos_client_patcher.stop()

    def test_validate_counter_value(self):
        """Test counter value validation"""
        service = VisitorCounterService(self.config, self.logger)
        self.assertTrue(service._validate_counter_value(0))
        self.assertTrue(service._validate_counter_value(10))
        self.assertFalse(service._validate_counter_value(-1))
        self.assertFalse(service._validate_counter_value("not-an-int"))
        self.assertFalse(service._validate_counter_value(None))

    def test_increment_counter_success(self):
        """Test successful counter increment"""
        # Setup mock response
        current_item = {
            'id': self.config.counter_id,
            'visitorCount': self.config.partition_key,
            'count': 42
        }
        self.mock_container.read_item.return_value = current_item

        service = VisitorCounterService(self.config, self.logger)
        new_count, status_code = service.increment_counter(self.correlation_id)

        # Verify expected outcome
        self.assertEqual(new_count, 43)
        self.assertEqual(status_code, 200)

        # Verify correct item was updated
        self.mock_container.read_item.assert_called_once_with(
            item=self.config.counter_id,
            partition_key=self.config.partition_key
        )

        # Verify update contains expected fields
        call_args = self.mock_container.upsert_item.call_args[0][0]
        self.assertEqual(call_args['count'], 43)
        self.assertEqual(call_args['correlationId'], self.correlation_id)
        self.assertEqual(call_args['lastModifiedBy'], 'VisitorCounterService')
        self.assertIn('lastUpdated', call_args)

    def test_increment_counter_invalid_value(self):
        """Test handling invalid counter value"""
        current_item = {
            'id': self.config.counter_id,
            'visitorCount': self.config.partition_key,
            'count': "not-a-number"  # Invalid value
        }
        self.mock_container.read_item.return_value = current_item

        service = VisitorCounterService(self.config, self.logger)
        new_count, status_code = service.increment_counter(self.correlation_id)

        # Verify expected outcome for error condition
        self.assertIsNone(new_count)
        self.assertEqual(status_code, 500)

    def test_increment_counter_not_found(self):
        """Test creating counter when not found"""
        # Mock resource not found error
        self.mock_container.read_item.side_effect = exceptions.CosmosResourceNotFoundError()

        service = VisitorCounterService(self.config, self.logger)
        new_count, status_code = service.increment_counter(self.correlation_id)

        # Verify expected outcome for new counter
        self.assertEqual(new_count, 1)
        self.assertEqual(status_code, 201)

        # Verify create was called with correct initial values
        call_args = self.mock_container.create_item.call_args[1]['body']
        self.assertEqual(call_args['id'], self.config.counter_id)
        self.assertEqual(call_args['visitorCount'], self.config.partition_key)
        self.assertEqual(call_args['count'], 1)
        self.assertEqual(call_args['correlationId'], self.correlation_id)
        self.assertEqual(call_args['createdBy'], 'VisitorCounterService')
        self.assertIn('created', call_args)

    def test_increment_counter_creation_failure(self):
        """Test creation failure handling"""
        # Mock resource not found followed by creation failure
        self.mock_container.read_item.side_effect = exceptions.CosmosResourceNotFoundError()
        self.mock_container.create_item.side_effect = Exception("Creation failed")

        service = VisitorCounterService(self.config, self.logger)
        new_count, status_code = service.increment_counter(self.correlation_id)

        # Verify expected outcome for error condition
        self.assertIsNone(new_count)
        self.assertEqual(status_code, 500)

    def test_increment_counter_concurrency_conflict(self):
        """Test concurrency conflict handling"""
        # Mock concurrency conflict
        self.mock_container.read_item.side_effect = exceptions.CosmosAccessConditionFailedError()

        service = VisitorCounterService(self.config, self.logger)
        new_count, status_code = service.increment_counter(self.correlation_id)

        # Verify expected outcome after max retries
        self.assertIsNone(new_count)
        self.assertEqual(status_code, 409)

        # Verify we tried max retries
        self.assertEqual(self.mock_container.read_item.call_count, MAX_RETRIES)

    def test_increment_counter_unexpected_error(self):
        """Test unexpected error handling"""
        # Mock unexpected error
        self.mock_container.read_item.side_effect = Exception("Unexpected error")

        service = VisitorCounterService(self.config, self.logger)
        new_count, status_code = service.increment_counter(self.correlation_id)

        # Verify expected outcome for error condition
        self.assertIsNone(new_count)
        self.assertEqual(status_code, 500)

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_get_counter_success(self, _):
        """Test successful get counter"""
        current_item = {
            'id': self.config.counter_id,
            'visitorCount': self.config.partition_key,
            'count': 42
        }
        self.mock_container.read_item.return_value = current_item

        service = VisitorCounterService(self.config, self.logger)
        count, status_code = await service.get_counter(self.correlation_id)

        # Verify expected outcome
        self.assertEqual(count, 42)
        self.assertEqual(status_code, 200)

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_get_counter_not_found(self, _):
        """Test get counter when not found"""
        # Mock resource not found error
        self.mock_container.read_item.side_effect = exceptions.CosmosResourceNotFoundError()

        service = VisitorCounterService(self.config, self.logger)
        count, status_code = await service.get_counter(self.correlation_id)

        # Verify expected outcome (0 count, but success status)
        self.assertEqual(count, 0)
        self.assertEqual(status_code, 200)

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_get_counter_invalid_value(self, _):
        """Test get counter with invalid value"""
        current_item = {
            'id': self.config.counter_id,
            'visitorCount': self.config.partition_key,
            'count': "not-a-number"  # Invalid value
        }
        self.mock_container.read_item.return_value = current_item

        service = VisitorCounterService(self.config, self.logger)
        count, status_code = await service.get_counter(self.correlation_id)

        # Verify expected outcome for error condition
        self.assertIsNone(count)
        self.assertEqual(status_code, 500)

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_get_counter_unexpected_error(self, _):
        """Test get counter with unexpected error"""
        # Mock unexpected error
        self.mock_container.read_item.side_effect = Exception("Unexpected error")

        service = VisitorCounterService(self.config, self.logger)
        count, status_code = await service.get_counter(self.correlation_id)

        # Verify expected outcome for error condition
        self.assertIsNone(count)
        self.assertEqual(status_code, 500)


class TestHelperFunctions(unittest.TestCase):
    """Tests for helper functions"""

    def test_setup_logging(self):
        """Test logging setup"""
        logger = setup_logging()
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.level, logging.INFO)

        # Verify handlers are attached
        console_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        self.assertGreaterEqual(len(console_handlers), 1)

    @mock.patch.dict(os.environ, {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=00000000-0000-0000-0000-000000000000"})
    def test_setup_logging_with_app_insights(self):
        """Test logging setup with App Insights enabled"""
        # This is more of an integration test, we're just verifying it doesn't crash
        logger = setup_logging()
        self.assertIsInstance(logger, logging.Logger)

    def test_generate_correlation_id_new(self):
        """Test generating new correlation ID"""
        req = func.HttpRequest(
            method='GET',
            url='/api/counter',
            body=None,
            headers={}
        )

        correlation_id = generate_correlation_id(req)

        # Verify correlation ID format (64 char hex)
        self.assertIsNotNone(correlation_id)
        self.assertEqual(len(correlation_id), 64)
        self.assertTrue(all(c in '0123456789abcdef' for c in correlation_id))

    def test_generate_correlation_id_from_header(self):
        """Test using existing correlation ID from header"""
        existing_id = "a" * 64  # Valid correlation ID
        req = func.HttpRequest(
            method='GET',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': existing_id}
        )

        correlation_id = generate_correlation_id(req)

        # Verify correlation ID is preserved
        self.assertEqual(correlation_id, existing_id)

    def test_generate_correlation_id_invalid_header(self):
        """Test handling invalid correlation ID in header"""
        invalid_id = "invalid-id"  # Not a valid format
        req = func.HttpRequest(
            method='GET',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': invalid_id}
        )

        correlation_id = generate_correlation_id(req)

        # Verify new correlation ID is generated
        self.assertNotEqual(correlation_id, invalid_id)
        self.assertEqual(len(correlation_id), 64)


class TestHttpTrigger(unittest.TestCase):
    """Integration tests for HTTP trigger functions"""

    def setUp(self):
        self.correlation_id = "a" * 64  # Valid correlation ID

        # Setup environment for tests
        self.env_patcher = mock.patch.dict(os.environ, {
            "COSMOS_ENDPOINT": "https://example.cosmos.azure.com",
            "COSMOS_KEY": "valid-key",
            "COSMOS_DATABASE": "TestDB",
            "COSMOS_CONTAINER": "TestContainer",
            "COSMOS_PARTITION_KEY": "TestPartition",
            "COSMOS_COUNTER_ID": "TestCounter"
        })
        self.env_patcher.start()

        # Patch CosmosConfig to avoid actual database creation
        self.config_patcher = mock.patch('function_app.CosmosConfig.from_env')
        self.mock_config = self.config_patcher.start()
        self.mock_config.return_value = CosmosConfig(
            endpoint="https://example.cosmos.azure.com",
            key="valid-key",
            database_name="TestDB",
            container_name="TestContainer",
            partition_key="TestPartition",
            counter_id="TestCounter"
        )

        # Patch VisitorCounterService methods
        self.service_patcher = mock.patch('function_app.VisitorCounterService')
        self.mock_service = self.service_patcher.start()
        self.mock_service_instance = mock.MagicMock()
        self.mock_service.return_value = self.mock_service_instance

    def tearDown(self):
        self.env_patcher.stop()
        self.config_patcher.stop()
        self.service_patcher.stop()

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_options_request(self, _):
        """Test OPTIONS request handling"""
        req = func.HttpRequest(
            method='OPTIONS',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        response = await main(req)

        # Verify expected response
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_body(), b"")

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_get_request_success(self, _):
        """Test successful GET request"""
        req = func.HttpRequest(
            method='GET',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        # Mock service response
        self.mock_service_instance.get_counter.return_value = (42, 200)

        response = await main(req)

        # Verify service was called correctly
        self.mock_service_instance.get_counter.assert_called_once_with(self.correlation_id)

        # Verify expected response
        self.assertEqual(response.status_code, 200)
        response_json = json.loads(response.get_body())
        self.assertEqual(response_json['count'], 42)

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_get_request_error(self, _):
        """Test GET request with error"""
        req = func.HttpRequest(
            method='GET',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        # Mock service error response
        self.mock_service_instance.get_counter.return_value = (None, 500)

        response = await main(req)

        # Verify service was called correctly
        self.mock_service_instance.get_counter.assert_called_once_with(self.correlation_id)

        # Verify expected error response
        self.assertEqual(response.status_code, 500)
        response_json = json.loads(response.get_body())
        self.assertEqual(response_json['error'], 'Error retrieving count')

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_post_request_success(self, _):
        """Test successful POST request"""
        req = func.HttpRequest(
            method='POST',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        # Mock service response
        self.mock_service_instance.increment_counter.return_value = (43, 200)

        response = await main(req)

        # Verify service was called correctly
        self.mock_service_instance.increment_counter.assert_called_once_with(self.correlation_id)

        # Verify expected response
        self.assertEqual(response.status_code, 200)
        response_json = json.loads(response.get_body())
        self.assertEqual(response_json['count'], 43)

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_post_request_created(self, _):
        """Test POST request with new counter created"""
        req = func.HttpRequest(
            method='POST',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        # Mock service response for new counter creation
        self.mock_service_instance.increment_counter.return_value = (1, 201)

        response = await main(req)

        # Verify service was called correctly
        self.mock_service_instance.increment_counter.assert_called_once_with(self.correlation_id)

        # Verify expected response
        self.assertEqual(response.status_code, 201)
        response_json = json.loads(response.get_body())
        self.assertEqual(response_json['count'], 1)

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_post_request_error(self, _):
        """Test POST request with error"""
        req = func.HttpRequest(
            method='POST',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        # Mock service error response
        self.mock_service_instance.increment_counter.return_value = (None, 500)

        response = await main(req)

        # Verify service was called correctly
        self.mock_service_instance.increment_counter.assert_called_once_with(self.correlation_id)

        # Verify expected error response
        self.assertEqual(response.status_code, 500)
        response_json = json.loads(response.get_body())
        self.assertEqual(response_json['error'], 'Error incrementing count')

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_unsupported_method(self, _):
        """Test unsupported HTTP method"""
        req = func.HttpRequest(
            method='PUT',  # Unsupported method
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        response = await main(req)

        # Verify expected error response
        self.assertEqual(response.status_code, 405)
        response_json = json.loads(response.get_body())
        self.assertEqual(response_json['error'], 'Method not allowed')

    @mock.patch('asyncio.sleep', return_value=None)
    async def test_config_error(self, _):
        """Test handling of configuration errors"""
        req = func.HttpRequest(
            method='GET',
            url='/api/counter',
            body=None,
            headers={'x-correlation-id': self.correlation_id}
        )

        # Mock configuration error
        self.mock_config.side_effect = ValueError("Configuration error")

        response = await main(req)

        # Verify expected error response
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_body(), b"Server configuration error")


if __name__ == '__main__':
    unittest.main()
