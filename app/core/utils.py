import uuid

def generate_cuid():
    """Generate a unique string ID (CUID-like)."""
    return str(uuid.uuid4()) 