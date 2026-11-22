from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import event 
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, create_engine, select


engine = create_engine(
    "sqlite:///noticeboard.db", connect_args = {"check_same_thread": False}
)


@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


class CategoryCreate(SQLModel):
    name: str

class Category(CategoryCreate, table = True):
    id: Optional[int] = Field(default = None, primary_key = True)


class NoticeCreate(SQLModel):
    title: str
    message: str
    category_id: int 


class Notice(NoticeCreate, table = True):
    id: Optional[int] = Field(default = None, primary_key = True)
    created_at: datetime = Field(
        default_factory = lambda: datetime.now(timezone.utc)
    )


SQLModel.metadata.create_all(engine)

app = FastAPI(title = "Noticeboard API")

def get_session():
    with Session(engine) as session:
        yield session

SessionDep = Annotated[Session, Depends(get_session)]

@app.get("/")
def home():
    return{"message": "Welcome to the Noticeboard API!"}


@app.get("/categories")
def list_categories(session:SessionDep):
    return session.exec(select(Category)).all()

@app.post("/categories", status_code=201)
def create_category(data: CategoryCreate, session: SessionDep):
    category = Category.model_validate(data)
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@app.post("/categories", status_code=201)
def create_category(data: CategoryCreate, session: SessionDep):
    category = Category.model_validate(data)
    session.add(category)
    session.commit()
    session.refresh(category)
    return category

    @app.delete("/categories/{category_id}", status_code=204)
    def delete_category(category_id: int, session: SessionDep):
        category = session.get(Category, category_id)
        if not category:
            raise HTTPException(status_code = 404, detail ="Category not found")
        try: 
            session.delete(category)
            session.commit()
        except IntegrityError:
            raise HTTPException(
                status_code= 400,
                detail= "Cannot delete category with associated notices",

            )   

@app.get ("/notices")
def list_notices(session: SessionDep):
    return session.exec(select(Notice).order_by(Notice.created_at,desc())).all()

@app.post("/notice", status_code=201)
def create_notice(data: NoticeCreate, session: SessionDep):
    notice = Notice.model_validate(data)
    session.add(notice) 
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code = 400,
            detail= "Category doesnt not exist. Please provide a valid category_id.",
        )
    session.refresh(notice)
    return notice

@app.get("/notices/{notice_id}")
def update_notice(notice_id: int, data: NoticeCreate, session: SessionDep):
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    notice.title = data. title
    notice.message = data. category_id
    session,add(notice)
    try: 
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code= 400,
            detail="Category doesnot exist. Please provide a valid category_id",
        )
    session.refresh(notice)
    return notice


@app.delete("/notices/{notice_id}", status_code=204)
def delete_notice(notice_id: int, session: SessionDep):
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail= "Notice not found")
    session.delete(notice)
    session.commit()        

