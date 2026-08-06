from pydantic import BaseModel, Field


class UserWithRoles(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    roles: list[str]


class RoleAssignmentRequest(BaseModel):
    role_name: str


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8)
