from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    role = Column(String, default="user")  # "user" или "admin"
    courses = relationship('Course', back_populates='user')
    progress = relationship('Progress', back_populates='user')

class Course(Base):
    __tablename__ = 'courses'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship('User', back_populates='courses')
    lectures = relationship('Lecture', back_populates='course')

class Lecture(Base):
    __tablename__ = 'lectures'
    
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    course_id = Column(Integer, ForeignKey('courses.id'))
    course = relationship('Course', back_populates='lectures')
    blocks = relationship('Block', back_populates='lecture')

class Block(Base):
    __tablename__ = 'blocks'
    
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    lecture_id = Column(Integer, ForeignKey('lectures.id'))
    lecture = relationship('Lecture', back_populates='blocks')

class ScheduleItem(Base):
    __tablename__ = 'schedule_items'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    date = Column(String, nullable=False)
    time = Column(String, nullable=False)
    block_id = Column(Integer, ForeignKey('blocks.id'))
    user = relationship('User')
    block = relationship('Block')

class Progress(Base):
    __tablename__ = 'progress'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    course_id = Column(Integer, ForeignKey('courses.id'))
    completed_blocks = Column(Integer, default=0)
    total_blocks = Column(Integer, nullable=False)
    user = relationship('User', back_populates='progress')

class UserStatistics(Base):
    __tablename__ = 'user_statistics'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    quizzes_taken = Column(Integer, default=0)
    average_score = Column(Integer, default=0)