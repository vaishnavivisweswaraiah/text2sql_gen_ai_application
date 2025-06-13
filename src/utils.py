from sqlalchemy.engine import create_engine
import os
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from langchain.prompts import PromptTemplate
from langchain.chains.llm import LLMChain
from langchain_openai import ChatOpenAI
from langchain.output_parsers import ResponseSchema, StructuredOutputParser
from dotenv import load_dotenv
load_dotenv()
import hashlib


def get_fav_key(question: str) -> str:
    # one single naming convention
    h = hashlib.md5(question.encode()).hexdigest()
    return f"fav_ind__{h}"

# Establish connection to Databricks
def get_databricks_engine():
    conn_str = (
        f"databricks://token:{os.getenv('DATABRICKS_ACCESS_TOKEN')}"
        f"@{os.getenv('DATABRICKS_SERVER_HOSTNAME')}?http_path={os.getenv('DATABRICKS_HTTP_PATH')}"
    )
    return create_engine(conn_str)

# Extract specific code blocks (SQL/Mermaid) from LLM response
def extract_code_block(response: str, code_type: str) -> str:
    start = response.find(f"```{code_type}") + len(f"```{code_type}")
    end = response.find("```", start)
    return response[start:end].strip()

# Render Mermaid diagram in Streamlit
def render_mermaid_diagram(code: str) -> None:
    code_escaped = code.replace("\\", "\\\\").replace("`", "\\`")
    components.html(f"""
        <div style='width: 100%; height: 800px; overflow: auto;'>
            <pre class='mermaid'>{code_escaped}</pre>
        </div>
        <script type='module'>
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
            mermaid.initialize({{ startOnLoad: true }});
        </script>
    """, height=800)
    

# Basic LLM call returning raw text
def run_basic_llm(template_string: str, model="gpt-4o-mini", temperature=0, **kwargs) -> str:
    prompt_template = PromptTemplate.from_template(template_string)

    ### Defining the LLM chain
    llm_chain = LLMChain(
        llm=ChatOpenAI(model=model,temperature=temperature),
        prompt=prompt_template
    )

    response =  llm_chain.invoke(kwargs)
    output = response['text']    
    return output

# LLM call with structured output parser
def run_structured_llm(template_string: str, output_parser: StructuredOutputParser, model="gpt-4o-mini", temperature=0, **kwargs):
    prompt_template = PromptTemplate.from_template(template_string)
    chain = LLMChain(
        llm=ChatOpenAI(model=model, temperature=temperature),
        prompt=prompt_template,
        output_parser=output_parser
    )
    return chain.invoke({**kwargs, "format_instructions": output_parser.get_format_instructions()})

# Fetch catalogs, schemas, tables from Databricks
@st.cache_data
def fetch_table_metadata():
    engine = get_databricks_engine()
    catalogs_df = pd.read_sql("SHOW CATALOGS", engine)
    catalogs = catalogs_df["catalog"].tolist()

    all_tables = []

    for catalog in catalogs:
        try:
            schemas_df = pd.read_sql(f"SHOW SCHEMAS IN {catalog}", engine)
            schemas = schemas_df["databaseName"].tolist()

            for schema in schemas:
                try:
                    tables_df = pd.read_sql(f"SHOW TABLES IN {catalog}.{schema}", engine)
                    result = tables_df[["tableName"]].copy()
                    result["catalog"] = catalog
                    result["schema"] = schema
                    all_tables.append(result)
                except Exception as e:
                    print(f"❌ Failed to get tables from {catalog}.{schema}: {e}")
        except Exception as e:
            print(f"❌ Failed to get schemas from {catalog}: {e}")

    return pd.concat(all_tables, ignore_index=True)[["catalog", "schema", "tableName"]]

# Extract schema structure + sample data + categorical info
@st.cache_data
def summarize_table_schema(catalog, schema, tables):
    engine = get_databricks_engine()
    full_schema = ""
    for table in tables:
        stmt = pd.read_sql(f"SHOW CREATE TABLE `{catalog}`.{schema}.{table}", engine)['createtab_stmt'][0].split("USING")[0]
        string_cols = pd.read_sql(f"DESCRIBE TABLE `{catalog}`.{schema}.{table}", engine)
        strings = string_cols[string_cols['data_type'] == 'string']['col_name'].tolist()
        if strings:
            unions = [
                f"SELECT '{col}' AS column_name, COUNT(DISTINCT {col}) AS cnt, ARRAY_AGG(DISTINCT {col}) AS values FROM `{catalog}`.{schema}.{table}"
                for col in strings
            ]
            df_cat = pd.read_sql(" UNION ALL ".join(unions), engine)
            df_cat = df_cat[df_cat['cnt'] <= 20].drop(columns='cnt')
            cat_info = df_cat.to_string(index=False) if not df_cat.empty else "No Categorical Fields"
        else:
            cat_info = "No Categorical Fields"
        sample = pd.read_sql(f"SELECT * FROM `{catalog}`.{schema}.{table} LIMIT 2", engine).to_string(index=False)
        full_schema += f"{stmt}\n{sample}\n\nCategorical Fields:\n{cat_info}\n"
    return full_schema

# Create ERD diagram using LLM
@st.cache_data
@st.experimental_fragment
def generate_erd_mermaid(catalog, schema, tables):
    engine = get_databricks_engine()
    table_meta = {}
    for table in tables:
        query = f"DESCRIBE TABLE `{catalog}`.{schema}.{table}"
        df = pd.read_sql(sql=query,con=engine)
        cols = df['col_name'].tolist()
        col_types = df['data_type'].tolist()
        cols_dict = [f"{col} : {col_type}" for col,col_type in zip(cols,col_types)]
        table_meta[table] = cols_dict
    prompt = """
            You are an expert in designing Entity Relationship Diagrams (ERDs) for databases.

            Your task is to analyze the provided table schema and generate valid Mermaid code that represents the ERD. The diagram should include:
            - All selected tables
            - Their columns and data types
            - Any clear relationships between tables

            The schema is provided below (delimited by ##) in dictionary format:
            - Keys represent table names
            - Values are lists of columns with their data types

            ##
            {table_schema}
            ##

            Carefully validate the input and ensure the Mermaid code is accurate, well-structured, and easy to understand.

            Return only the final Mermaid code for the ERD.
            """

    return run_basic_llm(template_string=prompt,model="gpt-4o-mini",temperature=0,table_schema=table_meta)

# Generate initial SQL from question
@st.cache_data
@st.experimental_fragment
def generate_initial_sql(question, table_schema):
    prompt = """
    Create a valid SQL query in Databricks SQL syntax to answer the user's question.
    Use full schema references, appropriate data types, and clean joins.
    SCHEMA: ## {table_schema} ##
    QUESTION: ## {question} ##
    Only return the SQL code.
    """
    return run_basic_llm(prompt, question=question, table_schema=table_schema)

# Extend SQL with additional logic using previous result
@st.cache_data
@st.experimental_fragment
def enhance_sql_with_cte(question, table_schema, sql_code):
    prompt = """
    Given this SQL (named 'MASTER'), wrap it in a WITH clause.
    Then generate a new SQL query that uses it to answer a follow-up question.
    SQL_CODE: ## {sql_code} ##
    SCHEMA: ## {table_schema} ##
    QUESTION: ## {question} ##
    Return only the final SQL.
    """
    return run_basic_llm(prompt, sql_code=sql_code, question=question, table_schema=table_schema)

# Run SQL and return data
@st.cache_data
@st.experimental_fragment
def execute_sql_query(query):
    return pd.read_sql(query, get_databricks_engine())

# Attempt to run SQL, return error if any
@st.experimental_fragment
def check_sql_validity(query):
    try:
        execute_sql_query(query)
        return "Successful"
    except Exception as e:
        return str(e)

# Use LLM to fix broken SQL
@st.experimental_fragment
def repair_faulty_sql(question, sql_code, table_schema, error_msg):
    prompt = """
    Modify the SQL query below to fix the error using the schema and error message provided.
    SCHEMA: ## {table_schema} ##
    ERROR: ## {error_msg} ##
    SQL_CODE: ## {sql_code} ##
    QUESTION: ## {question} ##
    Return only the corrected SQL.
    """
    return run_basic_llm(prompt, question=question, sql_code=sql_code, table_schema=table_schema, error_msg=error_msg)

# Validate and self-correct SQL
def validate_and_fix_sql(question, query, table_schema):
    status = check_sql_validity(query)
    return ("Correct", query) if status == "Successful" else ("Incorrect", repair_faulty_sql(question, query, table_schema, status))

# Generate structured business questions
@st.cache_data
@st.experimental_fragment
def generate_analysis_questions(table_schema):
    schema = ResponseSchema(
        name="business_questions",
        description="List of relevant business analysis questions based on the schema"
    )
    parser = StructuredOutputParser.from_response_schemas([schema])
    prompt = """
            Using the provided SCHEMA (delimited by ##), analyze the relationships between the tables and generate the **top 3 practical and insightful "quick analysis" questions**. These questions should be designed to be answerable using Databricks SQL and should reflect the type of day-to-day inquiries a product manager, data analyst, or business stakeholder might explore.

            Each question should be:
            - Rooted in the relationships and structure of the schema.
            - Relevant to real-world product or business performance analysis.
            - Framed to extract actionable insights (e.g., user behavior, conversion trends, operational efficiency, revenue impact, etc.).
            - Don't include question numbers in the genreated question

            SCHEMA:
            ##
            {table_schema}
            ##

            The final output must be structured as a nested JSON object with the following format:
            {format_instructions}

            Ensure the questions are clearly worded and thoughtfully derived from the underlying schema.
            """
    return run_structured_llm(prompt, parser, table_schema=table_schema)

# Log query to user history
@st.experimental_fragment
def log_user_query(user_name, question, query, is_favorite):
    engine = get_databricks_engine()
    query_str = f"INSERT INTO hive_metastore.dev_tools.sqlgen_user_query_history VALUES ('{user_name}', current_timestamp(), '{question}', \"{query}\", {is_favorite})"
    pd.read_sql(query_str, engine)

# Fetch saved queries for user
@st.cache_data
def get_user_history(user_name,selected_schema):
    query = f"""
    SELECT * FROM hive_metastore.dev_tools.sqlgen_user_query_history
    WHERE user_name = '{user_name}' AND timestamp > current_date - 20 AND query LIKE '%{selected_schema}%'
    ORDER BY timestamp DESC
    """
    return pd.read_sql(query, get_databricks_engine())
