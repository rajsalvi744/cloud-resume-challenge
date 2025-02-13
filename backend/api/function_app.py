import logging
import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

import azure.functions as func
from azure.cosmos import CosmosClient, exceptions
from azure.core.exceptions import AzureError
from opencensus.ext.azure.log_exporter import AzureLogHandler

app = func.FunctionApp()

# Configure logging
logger = logging.getLogger(__name__)
app_insights_connection = os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING')

# Add Application Insights handler if connection string is available
if app_insights_connection:
    logger.addHandler(AzureLogHandler(connection_string=app_insights_connection))
else:
    # Fallback to basic logging
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

@dataclass
class CosmosConfig:
    """Configuration for Cosmos DB"""
    endpoint: str
    key: str
    database_name: str
    container_name: str
    partition_key: str
    counter_id: str

class VisitorCounterService:
    def __init__(self, config: CosmosConfig):
        """Initialize the visitor counter service with configuration"""
        self.config = config
        self.client = CosmosClient(
            url=config.endpoint,
            credential=config.key,
            connection_verify=True  # Enforce SSL verification
        )
        self.container = (self.client
                         .get_database_client(config.database_name)
                         .get_container_client(config.container_name))

    async def increment_counter(self) -> tuple[int, int]:
        """
        Increment the visitor counter with optimistic concurrency control
        Returns: Tuple of (new_count, status_code)
        """
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                item = self.container.read_item(
                    item=self.config.counter_id,
                    partition_key=self.config.partition_key
                )
                
                # Update counter with timestamp
                new_count = item['count'] + 1
                item.update({
                    'count': new_count
                })

                self.container.upsert_item(item)
                logger.info(f"Successfully incremented counter to {new_count}")
                return new_count, 200

            except exceptions.CosmosAccessConditionFailedError:
                retry_count += 1
                logger.warning(f"Concurrency conflict, attempt {retry_count}/{max_retries}")
                if retry_count == max_retries:
                    logger.error("Max retries reached for concurrency conflicts")
                    return None, 409
                continue

            except exceptions.CosmosResourceNotFoundError:
                logger.warning("Counter document not found, creating initial counter")
                try:
                    # Create initial counter document
                    initial_item = {
                        'id': self.config.counter_id,
                        'count': 1
                    }
                    self.container.create_item(body=initial_item)
                    return 1, 201

                except Exception as e:
                    logger.error(f"Failed to create initial counter: {str(e)}")
                    return None, 500

            except Exception as e:
                logger.error(f"Unexpected error incrementing counter: {str(e)}")
                return None, 500

def get_cosmos_config() -> Optional[CosmosConfig]:
    """Load and validate Cosmos DB configuration from environment variables"""
    required_env_vars = {
        'COSMOS_ENDPOINT': os.getenv('COSMOS_ENDPOINT'),
        'COSMOS_KEY': os.getenv('COSMOS_KEY'),
        'COSMOS_DATABASE': os.getenv('COSMOS_DATABASE', 'AzureResume'),
        'COSMOS_CONTAINER': os.getenv('COSMOS_CONTAINER', 'VisitorCounter'),
        'COSMOS_PARTITION_KEY': os.getenv('COSMOS_PARTITION_KEY', 'visitorCount'),
        'COSMOS_COUNTER_ID': os.getenv('COSMOS_COUNTER_ID', 'visitorCount')
    }

    # Validate all required environment variables are present
    missing_vars = [k for k, v in required_env_vars.items() if not v]
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return None

    return CosmosConfig(
        endpoint=required_env_vars['COSMOS_ENDPOINT'],
        key=required_env_vars['COSMOS_KEY'],
        database_name=required_env_vars['COSMOS_DATABASE'],
        container_name=required_env_vars['COSMOS_CONTAINER'],
        partition_key=required_env_vars['COSMOS_PARTITION_KEY'],
        counter_id=required_env_vars['COSMOS_COUNTER_ID']
    )

@app.function_name(name="VisitorCounter")
@app.route(route="counter", methods=["GET","POST"])
async def main(req: func.HttpRequest) -> func.HttpResponse:
    """Azure Function entry point for visitor counter"""
    # Log the request with correlation ID
    correlation_id = req.headers.get('x-correlation-id', 'unknown')
    logger.info(f"Processing request | Correlation ID: {correlation_id} | Method: {req.method} | URL: {req.url}")

    # Load configuration
    config = get_cosmos_config()
    if not config:
        return func.HttpResponse(
            "Server configuration error",
            status_code=500
        )
    
    service = VisitorCounterService(config)
    
    if req.method == "OPTIONS":
        # Handle CORS preflight
        return func.HttpResponse(
            "",
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": req.headers.get("Origin", "*"),
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type"
            }
        )
    
     # **GET Request - Return the current count**
    elif req.method == "GET":
        try:
            item = service.container.read_item(
                item=config.counter_id, partition_key=config.partition_key
            )
            return func.HttpResponse(
                str(item["count"]),
                status_code=200,
                headers={
                    "Content-Type": "text/plain",
                    "Access-Control-Allow-Origin": "*",  # <-- Add this
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",  # <-- Allow these methods
                    "Access-Control-Allow-Headers": "Content-Type"  # <-- Ensure headers are allowed
                }
            )
        except exceptions.CosmosResourceNotFoundError:
            return func.HttpResponse("0", status_code=200)
        except Exception as e:
            logger.error(f"Error fetching counter: {str(e)}")
            return func.HttpResponse("Internal server error", status_code=500)
    
    # Validate request method
    elif req.method == 'POST':
        try:
            # Initialize service and increment counter            
            count, status_code = await service.increment_counter()

            if status_code == 200 or status_code == 201:
                return func.HttpResponse(
                    str(count),
                    status_code=status_code,
                    headers={
                        'Cache-Control': 'no-store',
                        'Content-Type': 'text/plain',
                        "Access-Control-Allow-Origin": "*",  # Allow JavaScript requests
                    }
                )
            else:
                error_messages = {
                    409: "Conflict updating counter",
                    500: "Internal server error"
                }
                return func.HttpResponse(
                    error_messages.get(status_code, "Unknown error"),
                    status_code=status_code
                )

        except Exception as e:
            logger.error(f"Unhandled error in visitor counter: {str(e)}")
            return func.HttpResponse(
                "Internal server error",
                status_code=500
            )
    
    return func.HttpResponse(
        "Method not allowed. Use GET to fetch count or POST to increment.",
        status_code=405
    )