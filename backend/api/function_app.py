"""API handler module for processing requests and responses."""
import logging
import os
from typing import Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import hashlib
import re
import azure.functions as func
from azure.cosmos import CosmosClient, exceptions
from opencensus.ext.azure.log_exporter import AzureLogHandler


# Constants
MAX_RETRIES = 3
DEFAULT_DATABASE = 'AzureResume'
DEFAULT_CONTAINER = 'VisitorCounter'
DEFAULT_PARTITION_KEY = 'visitorCount'
DEFAULT_COUNTER_ID = 'visitorCount'
CORS_MAX_AGE = 86400  # 24 hours

app = func.FunctionApp()

@dataclass(frozen=True)
class CosmosConfig:
    """Configuration for Cosmos DB with immutable attributes and validation"""
    endpoint: str
    key: str
    database_name: str
    container_name: str
    partition_key: str
    counter_id: str

    def __post_init__(self):
        """Validate configuration after initialization"""
        if not self.endpoint.startswith(('https://', 'http://')):
            raise ValueError("Cosmos endpoint must be a valid URL")

        if not self.key:
            raise ValueError("Cosmos key cannot be empty")

        if not all([self.database_name, self.container_name, self.partition_key, self.counter_id]):
            raise ValueError("All Cosmos DB parameters must be non-empty strings")

    @classmethod
    def from_env(cls) -> 'CosmosConfig':
        """Create configuration from environment variables with validation"""
        required_env_vars = {
            'COSMOS_ENDPOINT': os.getenv('COSMOS_ENDPOINT'),
            'COSMOS_KEY': os.getenv('COSMOS_KEY'),
            'COSMOS_DATABASE': os.getenv('COSMOS_DATABASE', DEFAULT_DATABASE),
            'COSMOS_CONTAINER': os.getenv('COSMOS_CONTAINER', DEFAULT_CONTAINER),
            'COSMOS_PARTITION_KEY': os.getenv('COSMOS_PARTITION_KEY', DEFAULT_PARTITION_KEY),
            'COSMOS_COUNTER_ID': os.getenv('COSMOS_COUNTER_ID', DEFAULT_COUNTER_ID)
        }

        missing_vars = [k for k, v in required_env_vars.items() if not v]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

        # Clean and validate inputs
        endpoint = required_env_vars['COSMOS_ENDPOINT'].strip()
        key = required_env_vars['COSMOS_KEY'].strip()

        return cls(
            endpoint=endpoint,
            key=key,
            database_name=required_env_vars['COSMOS_DATABASE'].strip(),
            container_name=required_env_vars['COSMOS_CONTAINER'].strip(),
            partition_key=required_env_vars['COSMOS_PARTITION_KEY'].strip(),
            counter_id=required_env_vars['COSMOS_COUNTER_ID'].strip()
        )

class VisitorCounterService:
    """Service for managing visitor counter with proper error handling and validation"""

    def __init__(self, config: CosmosConfig, logger: logging.Logger):
        """Initialize the visitor counter service"""
        self.config = config
        self.logger = logger
        self.client = CosmosClient(
            url=config.endpoint,
            credential=config.key,
            connection_verify=True,
            retry_total=3,
            retry_backoff_factor=0.1
        )
        self.container = (self.client
                         .get_database_client(config.database_name)
                         .get_container_client(config.container_name))

    def _validate_counter_value(self, value: Any) -> bool:
        """Validate counter value"""
        return isinstance(value, int) and value >= 0

    def increment_counter(self, correlation_id: str) -> Tuple[Optional[int], int]:
        """Increment the visitor counter with optimistic concurrency control"""
        retry_count = 0

        while retry_count < MAX_RETRIES:
            try:
                # Read current counter value
                item = self.container.read_item(
                    item=self.config.counter_id,
                    partition_key=self.config.partition_key
                )

                # Validate counter value
                current_count = item.get('count', 0)
                if not self._validate_counter_value(current_count):
                    self.logger.error(
                        f"Invalid counter value found: {current_count} | "
                        f"Correlation ID: {correlation_id}"
                    )
                    return None, 500

                # Update counter with timestamp and metadata
                new_count = current_count + 1
                item.update({
                    'count': new_count,
                    'lastUpdated': datetime.now(timezone.utc).isoformat(),
                    'correlationId': correlation_id,
                    'lastModifiedBy': 'VisitorCounterService'
                })

                self.container.upsert_item(item)
                self.logger.info(
                    f"Counter incremented to {new_count} | "
                    f"Correlation ID: {correlation_id}"
                )
                return new_count, 200

            except exceptions.CosmosAccessConditionFailedError:
                retry_count += 1
                self.logger.warning(
                    f"Concurrency conflict | Attempt {retry_count}/{MAX_RETRIES} | "
                    f"Correlation ID: {correlation_id}"
                )
                if retry_count == MAX_RETRIES:
                    return None, 409
                continue

            except exceptions.CosmosResourceNotFoundError:
                self.logger.info(
                    f"Counter not found, creating initial counter | "
                    f"Correlation ID: {correlation_id}"
                )
                try:
                    initial_item = {
                        'id': self.config.counter_id,
                        'visitorCount': self.config.partition_key,
                        'count': 1,
                        'created': datetime.now(timezone.utc).isoformat(),
                        'correlationId': correlation_id,
                        'createdBy': 'VisitorCounterService'
                    }
                    self.container.create_item(body=initial_item)
                    return 1, 201

                except Exception as e:
                    self.logger.error(
                        f"Failed to create counter | Error: {str(e)} | "
                        f"Correlation ID: {correlation_id}"
                    )
                    return None, 500

            except Exception as e:
                self.logger.error(
                    f"Unexpected error | Error: {str(e)} | "
                    f"Correlation ID: {correlation_id}"
                )
                return None, 500

    async def get_counter(self, correlation_id: str) -> Tuple[Optional[int], int]:
        """Get the current counter value"""
        try:
            item = self.container.read_item(
                item=self.config.counter_id,
                partition_key=self.config.partition_key
            )
            count = item.get('count', 0)
            if not self._validate_counter_value(count):
                self.logger.error(
                    f"Invalid counter value | Value: {count} | "
                    f"Correlation ID: {correlation_id}"
                )
                return None, 500
            return count, 200

        except exceptions.CosmosResourceNotFoundError:
            return 0, 200

        except Exception as e:
            self.logger.error(
                f"Error fetching counter | Error: {str(e)} | "
                f"Correlation ID: {correlation_id}"
            )
            return None, 500

def setup_logging() -> logging.Logger:
    """Configure logging with Application Insights and secure defaults"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    # Add Application Insights handler if available
    app_insights_connection = os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING')
    if app_insights_connection:
        logger.addHandler(AzureLogHandler(
            connection_string=app_insights_connection
        ))

    # Add console handler for local debugging with sanitized output
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    )
    logger.addHandler(console_handler)

    return logger

def generate_correlation_id(req: func.HttpRequest) -> str:
    """Generate or validate correlation ID"""
    correlation_id = req.headers.get('x-correlation-id')

    if correlation_id:
        # Validate existing correlation ID format
        if not re.match(r'^[a-f0-9]{64}$', correlation_id):
            correlation_id = None

    if not correlation_id:
        # Generate new correlation ID
        correlation_id = hashlib.sha256(os.urandom(32)).hexdigest()

    return correlation_id

@app.function_name(name="VisitorCounter")
@app.route(route="counter", methods=["GET","POST"])
async def main(req: func.HttpRequest) -> func.HttpResponse:
    """Azure Function entry point for visitor counter with comprehensive security"""
    logger = setup_logging()

    # Generate or validate correlation ID
    correlation_id = generate_correlation_id(req)
    logger.info("Processing request | Method: %s | Correlation ID: %s", req.method, correlation_id)
    try:
        config = CosmosConfig.from_env()
    except ValueError as e:
        logger.error("Configuration error | Error: %s | Correlation ID: %s", str(e), correlation_id)
        return func.HttpResponse(
            "Server configuration error",
            status_code=500
        )

    service = VisitorCounterService(config, logger)


    if req.method == "OPTIONS":
        return func.HttpResponse(
            "",
            status_code=200
        )

    elif req.method == "GET":
        count, status_code = await service.get_counter(correlation_id)
        if status_code == 200:
            return func.HttpResponse(
                json.dumps({'count': count}),
                status_code=status_code,
                headers={
                    'Content-Type': 'application/json'
                }
            )
        return func.HttpResponse(
            json.dumps({'error': 'Error retrieving count'}),
            status_code=status_code
        )

    elif req.method == "POST":
        count, status_code = service.increment_counter(correlation_id)
        if status_code in (200, 201):
            return func.HttpResponse(
                json.dumps({'count': count}),
                status_code=status_code,
                headers={
                    'Content-Type': 'application/json'
                }
            )
        return func.HttpResponse(
            json.dumps({'error': 'Error incrementing count'}),
            status_code=status_code
        )

    return func.HttpResponse(
        json.dumps({'error': 'Method not allowed'}),
        status_code=405
    )
