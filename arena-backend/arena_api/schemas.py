import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Member(Model):
    name: str = Field(min_length=2, max_length=60)
    student_id: str = Field(pattern=r"^[A-Za-z0-9]{6,15}$")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if len(value) < 2 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid name")
        return value


class Registration(Model):
    team: str = Field(min_length=2, max_length=40)
    email: str = Field(min_length=3, max_length=120)
    phone: str = Field(default="", max_length=20)
    members: list[Member] = Field(min_length=3, max_length=4)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean true required")
        return value

    @field_validator("team")
    @classmethod
    def clean_team(cls, value):
        value = value.strip()
        if len(value) < 2 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid team name")
        return value

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email")
        return value

    @field_validator("members")
    @classmethod
    def distinct_members(cls, value):
        if len({m.student_id.casefold() for m in value}) != len(value):
            raise ValueError("Duplicate student ID")
        return value


class Login(Model):
    username: str = Field(min_length=1, max_length=30)
    password: SecretStr = Field(min_length=1, max_length=128)


class PasswordChange(Model):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=8, max_length=128)


class Credentials(Model):
    username: str = Field(pattern=r"^[A-Za-z0-9_-]{4,30}$")
    temporary_password: SecretStr | None = Field(default=None, min_length=8, max_length=128)


class ResetPassword(Model):
    temporary_password: SecretStr | None = Field(default=None, min_length=8, max_length=128)


class AccountLock(Model):
    locked: bool


class Recovery(Model):
    username: str = Field(min_length=1, max_length=30)
    email: str = Field(min_length=3, max_length=120)


class TimeControl(Model):
    match_ms: Literal[60000, 300000] | None = None
    turn_ms: Literal[15000, 30000, 60000, 300000] | None = None

    @model_validator(mode="after")
    def one_clock(self):
        if self.match_ms is not None and self.turn_ms is not None:
            raise ValueError("Chọn tổng thời gian match_ms hoặc turn_ms cũ; không gửi cả hai")
        return self


class Benchmark(TimeControl):
    submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ruleset_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class Match(TimeControl):
    red_submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    blue_submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    delegate_timeout_claims: Literal[True]
    ruleset_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')

    @field_validator("delegate_timeout_claims", mode="before")
    @classmethod
    def exact_delegation(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean true required")
        return value


class UserView(Model):
    id: str
    username: str
    role: Literal["admin", "team"]
    team_id: str | None
    must_change_password: bool
    locked: bool
    delivered: bool
    created_ms: int


class SessionView(Model):
    user: UserView
    csrf_token: str
    expires_ms: int


class IssuedCredential(Model):
    account: UserView
    temporary_password: str
    credential_visibility: Literal["this_response_only"]


class RegistrationReceipt(Model):
    id: str
    status: Literal["pending", "rejected"]


class TeamView(Model):
    id: str
    name: str
    email: str
    phone: str
    members: list[Member]
    created_ms: int
    archived_ms: int | None = None
    revision: int = 1


class AdminTeamCreate(Model):
    profile: Registration
    username: str = Field(pattern=r"^[A-Za-z0-9_-]{4,30}$")
    temporary_password: SecretStr | None = Field(default=None, min_length=8, max_length=128)


class Revision(Model):
    expected_revision: int = Field(ge=1)


class AdminTeamEdit(Registration):
    expected_revision: int = Field(ge=1)


class CompetitionCreate(TimeControl):
    title: str = Field(min_length=3, max_length=100)
    max_teams: int = Field(default=16, ge=2, le=32)
    ruleset_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if len(value) < 3 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid competition title")
        return value


class CompetitionEdit(CompetitionCreate):
    expected_revision: int = Field(ge=1)


class CompetitionEntry(Model):
    submission_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    benchmark_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    delegate_timeout_claims: Literal[True]

    @field_validator("delegate_timeout_claims", mode="before")
    @classmethod
    def exact_delegation(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean true required")
        return value
