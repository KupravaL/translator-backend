# Translator Backend

This is the backend server for the Translator application, providing API endpoints for document translation, user balance management, and payment processing.

## Features

- **Document Translation**: Translate documents from one language to another
- **User Balance System**: Track and manage user translation credit balance
- **Payment Processing**: Handle payment requests for purchasing translation credits
- **Admin Dashboard**: Approve or reject payment requests
- **Email Notifications**: Send invoice and payment instructions via email

## Technologies

- **FastAPI**: High-performance web framework for building APIs
- **SQLAlchemy**: SQL toolkit and ORM
- **Pydantic**: Data validation and settings management
- **JWT Authentication**: Secure API endpoints
- **Resend**: Email delivery service
- **PostgreSQL**: Database for storing user data and translations

## Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL
- Resend API key

### Installation

1. Clone the repository
```bash
git clone https://github.com/YOUR_USERNAME/translator-backend.git
cd translator-backend
```

2. Create and activate a virtual environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies
```bash
pip install -r requirements.txt
```

4. Create a `.env` file based on `.env.example` and fill in your credentials

5. Initialize the database
```bash
python init_db.py
```

6. Start the server
```bash
uvicorn main:app --host=0.0.0.0 --port=8000 --reload
```

## API Documentation

When the server is running, you can access the API documentation at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Environment Variables

Required environment variables:
- `DATABASE_URL`: PostgreSQL connection string
- `RESEND_API_KEY`: API key for the Resend email service
- `CLERK_SECRET_KEY`: Secret key for Clerk authentication
- `API_BASE_URL`: Base URL for the backend API

## Admin Tools

For confirming payment requests:

```bash
python -m scripts.confirm_payment --order-id=ORDER_ID
```

To reject a payment:

```bash
python -m scripts.confirm_payment --order-id=ORDER_ID --reject
```

## License

[MIT](LICENSE)