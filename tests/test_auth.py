from httpx import AsyncClient


async def test_register_user(test_client: AsyncClient, test_user_data):
    response = await test_client.post("/auth/register", json=test_user_data)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == test_user_data["email"]
    assert data["username"] == test_user_data["username"]
    assert "hashed_password" not in data
    assert "id" in data


async def test_register_duplicate_email(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    dupe = {**test_user_data, "username": "someone_else"}
    response = await test_client.post("/auth/register", json=dupe)
    assert response.status_code == 400


async def test_register_duplicate_username(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    dupe = {**test_user_data, "email": "other@example.com"}
    response = await test_client.post("/auth/register", json=dupe)
    assert response.status_code == 400


async def test_login_success(test_client: AsyncClient, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    response = await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_login_invalid_credentials(test_client: AsyncClient):
    response = await test_client.post(
        "/auth/login", data={"username": "nonexistent", "password": "wrongpassword"}
    )
    assert response.status_code == 401


async def test_protected_route_requires_token(test_client: AsyncClient):
    response = await test_client.get("/patients/")
    assert response.status_code == 401


async def test_protected_route_rejects_bad_token(test_client: AsyncClient):
    response = await test_client.get(
        "/patients/", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401
