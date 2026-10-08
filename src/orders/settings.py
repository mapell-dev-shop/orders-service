import os

DATABASE_URL = os.environ.get("ORDERS_DATABASE_URL", "sqlite:///orders.db")
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
INVENTORY_URL = os.environ.get("INVENTORY_URL", "http://localhost:8002")
