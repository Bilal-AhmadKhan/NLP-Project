from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette import status
from .rag import user_question
from pydantic import BaseModel




app = FastAPI()


class qin(BaseModel):
    question : str 

class qresponse(BaseModel):
    response : str



@app.get('/')
def root():
    return(
        RedirectResponse(url = '/docs', status_code= status.HTTP_302_FOUND)
    )


@app.post('/ask', response_model = qresponse)
def ask_chatbot(question:qin):
    answer = user_question(question.question)
    return {
        'response' : answer
    }
    


