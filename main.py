from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import bcrypt
import jwt
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, create_engine, select


engine = create_engine(
    "sqlite:///noticeboard.db", connect_args={"check_same_thread": False}
)


@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


class CategoryCreate(SQLModel):
    name: str


class Category(CategoryCreate, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True)
    password_hash: str


class NoticeCreate(SQLModel):
    title: str
    message: str
    category_id: int


class Notice(NoticeCreate, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    category_id: int = Field(foreign_key="category.id")
    author_id: int = Field(foreign_key="user.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class UserCreate(SQLModel):
    username: str
    password: str


SQLModel.metadata.create_all(engine)

app = FastAPI(title="Noticeboard API")


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

SECRET_KEY = "change-this-to-a-long-random-text"
ALGORITHM = "HS256"
TOKEN_MINUTES = 120
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def create_token(user_id: int):
    expire = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_MINUTES)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep
):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@app.get("/")
def home():
    return {"message": "Welcome to the Noticeboard API!"}


@app.post("/register", status_code=201)
def register(data: UserCreate, session: SessionDep):
    if len(data.username) < 3:
        raise HTTPException(
            status_code=400, detail="Username must be at least 3 characters"
        )
    if not 4 <= len(data.password) <= 50:
        raise HTTPException(
            status_code=400, detail="Password must be 4 to 50 characters"
        )
    existing = session.exec(
        select(User).where(User.username == data.username)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
    hashed = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode()
    user = User(username=data.username, password_hash=hashed)
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"id": user.id, "username": user.username}


@app.post("/login")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep
):
    user = session.exec(
        select(User).where(User.username == form.username)
    ).first()
    if not user or not bcrypt.checkpw(
        form.password.encode(), user.password_hash.encode()
    ):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    return {"access_token": create_token(user.id), "token_type": "bearer"}


@app.get("/categories")
def list_categories(session: SessionDep, user: CurrentUser):
    return session.exec(select(Category)).all()


@app.post("/categories", status_code=201)
def create_category(
    data: CategoryCreate, session: SessionDep, user: CurrentUser
):
    category = Category.model_validate(data)
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@app.delete("/categories/{category_id}", status_code=204)
def delete_category(
    category_id: int, session: SessionDep, user: CurrentUser
):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    try:
        session.delete(category)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Cannot delete category with associated notices",
        )


@app.get("/notices")
def list_notices(session: SessionDep, user: CurrentUser):
    rows = session.exec(
        select(Notice, User.username)
        .join(User, Notice.author_id == User.id)
        .order_by(Notice.created_at.desc())
    ).all()
    return [
        {**notice.model_dump(), "author_name": name} for notice, name in rows
    ]


@app.post("/notices", status_code=201)
def create_notice(data: NoticeCreate, session: SessionDep, user: CurrentUser):
    notice = Notice.model_validate(data, update={"author_id": user.id})
    session.add(notice)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Category does not exist. Please provide a valid category_id.",
        )
    session.refresh(notice)
    return notice


@app.put("/notices/{notice_id}")
def update_notice(
    notice_id: int,
    data: NoticeCreate,
    session: SessionDep,
    user: CurrentUser,
):
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if notice.author_id != user.id:
        raise HTTPException(
            status_code=403, detail="You can only edit your own notices"
        )
    notice.title = data.title
    notice.message = data.message
    notice.category_id = data.category_id
    session.add(notice)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Category does not exist. Please provide a valid category_id.",
        )
    session.refresh(notice)
    return notice


@app.delete("/notices/{notice_id}", status_code=204)
def delete_notice(notice_id: int, session: SessionDep, user: CurrentUser):
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if notice.author_id != user.id:
        raise HTTPException(
            status_code=403, detail="You can only delete your own notices"
        )
    session.delete(notice)
    session.commit()