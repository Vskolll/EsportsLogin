from backend.telegram.client import create_client

async def create_qr_login():
    client = create_client()
    await client.connect()

    qr = await client.qr_login()
    return client, qr
