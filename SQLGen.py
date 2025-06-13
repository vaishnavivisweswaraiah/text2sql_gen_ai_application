
import streamlit as st
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader
import sys,os
import pandas as pd
import numpy as np

# Brining the python scripts from the src folder
sys.path.append(os.path.abspath('src'))
from src.utils import (
    fetch_table_metadata,
    generate_erd_mermaid,
    render_mermaid_diagram,
    extract_code_block,
    summarize_table_schema,
    generate_initial_sql,
    validate_and_fix_sql,
    execute_sql_query,
    enhance_sql_with_cte,
    log_user_query,
    get_user_history,
    get_fav_key,
    generate_analysis_questions
)


# Page Configuration
st.set_page_config(
    page_title="SQLGenerator",
    page_icon="🧠",
    layout="centered",
    initial_sidebar_state="expanded"
    )

# The App 
st.markdown("<h1 style='text-align: center; color: orange;'> SQLGen &#128202; </h1>", unsafe_allow_html=True)

st.markdown("<h7 style='text-align: center; color: white;'> Productivity Improvement AI tool for non technical staff when working with data stored in a traditional SQL database. </h7>", unsafe_allow_html=True)

# Adding the authentication
with open('authenticator.yml') as f:
    config = yaml.load(f, Loader=SafeLoader)

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

name, authentication_status, user_name = authenticator.login()

if authentication_status:
    authenticator.logout('Logout','main')
    st.write(f"Welcome *{name}*!")

    # Application logic from here
    # Selection catalog, Schema and Table in the Target database
    st.sidebar.image("artifacts/Databricks_Logo.png")
    result_table=fetch_table_metadata()
    df_databricks=pd.DataFrame(result_table)
    df_databricks.columns=['catalog','schema','table']

    #getting catalog to schema mapping for dynamically selecting only relevant schema for a given catalog
    catalog_schema_mapping_df=df_databricks.groupby(['catalog']).agg({'schema' : lambda x: list(np.unique(x))}).reset_index()

    # getting schema to table mapping for dynamically selecting only relevant tables for a given catalog and schema
    schema_table_mapping_df = df_databricks.groupby(["schema"]).agg({'table': lambda x: list(np.unique(x))}).reset_index()

    # Selecting the catalog using selectbox
    catalog= st.sidebar.selectbox("Select the catalog", options=df_databricks['catalog'].unique().tolist())

    # Selecting the schema 
    schema_candidate_list = catalog_schema_mapping_df[catalog_schema_mapping_df["catalog"]==catalog]["schema"].values[0]
    schema = st.sidebar.selectbox("Select the schema", options=schema_candidate_list)

    # Selecting the Tables
    table_candidate_list = schema_table_mapping_df[schema_table_mapping_df["schema"]==schema]["table"].values[0]
    table_list = st.sidebar.multiselect("Select the table", options= ["All"]+table_candidate_list)

    if 'All' in table_list:
        table_list=table_candidate_list
    
    if st.sidebar.checkbox(":orange[Proceed]"):        
        with st.expander(":orange[View the ER Diagram]"):
            response = generate_erd_mermaid(catalog,schema,table_list)
            if st.button("Regenerate"):
                # Creating the ER Diagram
                generate_erd_mermaid.clear()
                response = generate_erd_mermaid(catalog,schema,table_list)
                mermaid_code = extract_code_block(response,code_type='mermaid')
                render_mermaid_diagram(mermaid_code)
            else:
                mermaid_code = extract_code_block(response,code_type='mermaid')
                render_mermaid_diagram(mermaid_code)
        
        # Getting the schema and table
        table_schema=summarize_table_schema(catalog,schema,table_list)

        # Tabs for analysis modes
        tab1, tab2, tab3 = st.tabs(["🔍 Quick Analysis", "⭐ Favourites","📊 Deep Analysis",])
        ################################ Quick Analysis ######################################################
        with tab1:
            st.markdown("<h4 style='text-align: left; color: orange;'> Quick business focued questions based on selected choices </h4>", unsafe_allow_html=True)
            quick_analysis_questions = generate_analysis_questions(table_schema)
            if st.button("💡 Need New Analysis Ideas"):
                generate_analysis_questions.clear()
                quick_analysis_questions = generate_analysis_questions(table_schema)
            questions = quick_analysis_questions['text']['business_questions']
            selected_question = st.selectbox("Pick a question to analyze", options=questions)
            if st.checkbox('Analyze this question'):
                        st.write(f'#### {selected_question}')
                        response_sql_qa = generate_initial_sql(selected_question,table_schema)
                        response_sql_qa = extract_code_block(response_sql_qa,'sql')

                        #Self-correction loop
                        flag, response_sql_qa = validate_and_fix_sql(selected_question,response_sql_qa,table_schema)
                        while flag != 'Correct':
                            flag,response_sql_qa=validate_and_fix_sql(selected_question,response_sql_qa,table_schema)
                        
                        st.code(response_sql_qa)

                        col1, col2 = st.columns(2)

                        query_sample_data_1=col1.checkbox("Preview Sample Data", key="d-1024")
                        if query_sample_data_1:
                            df_query_qa = execute_sql_query(response_sql_qa)
                            col1.write(df_query_qa)

                        # Favorite tracking: one flag per question
                        fav_key = get_fav_key(selected_question)
                        if fav_key not in st.session_state:
                            st.session_state[fav_key] = False

                        # Save / status
                        fav_button = col2.button("⭐ Save to Favourites", key=f'd-133-{fav_key}')
                        if fav_button and not st.session_state[fav_key]:
                            log_user_query(name, selected_question, response_sql_qa, is_favorite=True)
                            st.session_state[fav_key] = True
                            get_user_history.clear()
                            fav_df = get_user_history(user_name=name, selected_schema=schema)
                            col2.success("Added to Favourites")
                        elif st.session_state[fav_key]:
                            col2.info("✅ Already saved")

        ################################# Favourite Section ##################################################
        with tab2:
            st.markdown("<h2 style='text-align: left; color: orange;'> Your Favourite </h2>", unsafe_allow_html=True)
            # Refresh button to reload the latest favourites from the database
            if st.button("🔄 Refresh Favourites", key="refresh_fav"):
                get_user_history.clear()

            fav_df = get_user_history(user_name=name, selected_schema=schema)
            if fav_df.empty:
                st.info("No saved queries yet.")
            else:
                # let the user pick one of their saved questions
                fav_question = st.selectbox(
                    "Select the question",
                    options=fav_df['question'].unique().tolist(),
                    key="fav_select"
                )     
                fav_sql = fav_df[fav_df['question']==fav_question]['query'].values[0]

                st.write(f"#### {fav_question}")
                st.code(fav_sql)

                col1, col2 = st.columns(2)
                # dynamic key so each checkbox is unique per question
                sample_key = get_fav_key(fav_question)

                # preview sample data
                fav_sample_data = col1.checkbox("Query Sample Data",key="sample_key")
                if fav_sample_data:
                    fav_query=execute_sql_query(fav_sql)
                    col1.write(fav_query)

        ################################# Deep Analysis ######################################################
        with tab3:
            st.markdown("<h2 style='text-align: left; color: orange;'> Deep Analysis - Ask any custom business question </h2>", unsafe_allow_html=True)
            d_question = st.text_area("Enter a deep-dive question here..")

            generate_sql_1 = st.checkbox("Generate SQL",key="dd-10001")
            if generate_sql_1:
                    response_sql_1 = generate_initial_sql(d_question,table_schema)
                    response_sql_1 = extract_code_block(response_sql_1,'sql')

                    #Self correction loop
                    flag,response_sql_1 = validate_and_fix_sql(d_question,response_sql_1,table_schema)
                    while flag != 'Correct':
                        flag, response_sql_1 = validate_and_fix_sql(d_question,response_sql_1,table_schema)
                
                    st.code(response_sql_1)

                    col1, col2 = st.columns(2)

                    query_sample_data_1=col1.checkbox("Query Sample Data", key="d-102")
                    if query_sample_data_1:
                        df_query_1 = execute_sql_query(response_sql_1)
                        col1.write(df_query_1)


                    # Favorite tracking: one flag per question
                    fav_key = get_fav_key(d_question)
                    if fav_key not in st.session_state:
                        st.session_state[fav_key] = False

                    # Save / status
                    fav_button = col2.button("⭐ Save to Favourites", key=f'd-13-{fav_key}')
                    if fav_button and not st.session_state[fav_key]:
                            log_user_query(name, d_question, response_sql_1, is_favorite=True)
                            st.session_state[fav_key] = True
                            get_user_history.clear()
                            fav_df = get_user_history(user_name=name, selected_schema=schema)
                            col2.success("Added to Favourites")
                    elif st.session_state[fav_key]:
                            col2.info("✅ Already saved")

                    #Building on top of existing query
                    build_1 = col1.checkbox("Build on top of this result?", key='d-21')
                    if build_1:
                        d_question_2 = st.text_area("Enter your question here...", key='d-23')

                        generate_sql_2 = st.checkbox("Generate SQL", key = 'd-24')
                        if generate_sql_2:
                            response_sql_2 = enhance_sql_with_cte(d_question_2,table_schema,generate_sql_2)
                            response_sql_2 = extract_code_block(response_sql_2,'sql')

                            #Self Correction loop
                            flag, response_sql_2 = validate_and_fix_sql(d_question_2,response_sql_2,table_schema)
                            while flag != 'Correct':
                                response_sql_2= validate_and_fix_sql(d_question_2,response_sql_2,table_schema)
                            st.code(response_sql_2)

                            col1, col2 = st.columns(2)
                            query_sample_data_2 = col1.checkbox("Query Sample Data", key='d-25')
                            if query_sample_data_2:
                                df_query_2 = execute_sql_query(response_sql_2)
                                col1.write(df_query_2)

                            # Favorite tracking: one flag per question
                            fav_key = get_fav_key(d_question_2)
                            if fav_key not in st.session_state:
                                st.session_state[fav_key] = False

                            # Save / status
                            fav_button = col2.button("⭐ Save to Favourites", key=f'd-143-{fav_key}')
                            if fav_button and not st.session_state[fav_key]:
                                    log_user_query(name, d_question_2, response_sql_2, is_favorite=True)
                                    st.session_state[fav_key] = True
                                    get_user_history.clear()
                                    fav_df = get_user_history(user_name=name, selected_schema=schema)
                                    col2.success("Added to Favourites")
                            elif st.session_state[fav_key]:
                                    col2.info("✅ Already saved")
        ############## SIDEBAR FAVOURITES PREVIEW ##############
        with st.sidebar.expander("⭐ Saved Queries"):
            if 'fav_df' in locals() and not fav_df.empty:
                for idx, row in fav_df.head(5).iterrows():
                    st.write(f"• {row['question']}")
            else:
                st.caption("No favorites saved yet.")
else:
    st.write("Please login to Continue")