from langchain.embeddings import Embeddings
from langchain_community.document_loaders import PyPDFLoader
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser


load_dotenv()

loader = PyPDFLoader('UGE-Policy.pdf')

documents = loader.load()

splitter = RecursiveCharacterTextSplitter(
    chunk_size = 1000,
    chunk_overlap = 200,
)

chunks = splitter.split_documents(documents)

embeddings = GoogleGenerativeAIEmbeddings(
    model= 'models/gemini-embedding-001'
)

vector_db = Chroma(
    persist_directory="./chroma_db",
    collection_name="pdf_documents",
    embedding_function=embeddings,
)

if vector_db._collection.count() == 0:
    vector_db.add_documents(chunks)
    print("Indexed PDF chunks.")
else:
    pass



llm = ChatGoogleGenerativeAI(
    model = "gemini-3.5-flash-lite",
    temperature = 0.1,
)

prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are a question-answering assistant.

Answer using only the supplied context.
If the answer is not contained in the context, say:
"I do not know based on the provided documents."

Do not invent information.
""",
        ),
        (
            "human",
            """
Context:
{context}

Question:
{question}
""",
        ),
    ]
)   

def format_docs(docs):
    return "\n\n".join(
        f"Source: {doc.metadata.get('source', 'Unknown')}\n"
        f"Page: {doc.metadata.get('page', 'Unknown')}\n"
        f"Content: {doc.page_content}"
        for doc in docs
    )

retriever = vector_db.as_retriever(
    search_kwargs = {'k' : 5}
)

rag_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough(),
    }
    | prompt
    | llm
    | StrOutputParser()
)


def user_question(question : str):
    return rag_chain.invoke(question)
