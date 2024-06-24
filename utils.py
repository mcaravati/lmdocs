from python_parsers import get_all_calls, get_all_imports, parse_commented_function, same_ast_with_reason, remove_docstring, replace_func
from get_code_docs import CodeData, get_reference_docs_custom_functions, get_shortened_docs
from prompts import SYSTEM_PROMPT, DOC_GENERATION_PROMPT
from constants import TOK_COUNT
from llm_inference import get_llm_output

import argparse
from argparse import RawTextHelpFormatter
import pandas as pd
import logging
import ast
import os
import math
from collections import Counter


def get_args():
    parser = argparse.ArgumentParser(formatter_class=RawTextHelpFormatter)
        
    parser.add_argument(
        "path",
        help="Path to the file/folder of project",
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action='store_true',
        help="Give out verbose logs",
    )

    parser.add_argument(
        "--openai_key",
        help="Your Open AI key",
    )

    parser.add_argument(
        "--groq_key",
        help="Your Groq key",
    )

    parser.add_argument(
        "--openai_key_env",
        help="Environment variable where Open AI key is stored",
    )

    parser.add_argument(
        "--groq_key_env",
        help="Environment variable where Groq key is stored",
    )

    parser.add_argument(
        "--openai_model",
        choices=['gpt-3.5-turbo', 'gpt-4-turbo', 'gpt-4o'],
        help="Which openAI model to use. Supported models are ['gpt-3.5-turbo', 'gpt-4-turbo', 'gpt-4o']"
    )

    parser.add_argument(
        "--groq_model",
        choices=['gemma-7b-it', 'mixtral-8x7b-32768', 'llama3-70b-8192', 'llama3-8b-8192'],
        help="Which Groq model to use. Supported models are ['gemma-7b-it', 'mixtral-8x7b-32768', 'llama3-70b-8192', 'llama3-8b-8192']"
    )

    parser.add_argument(
        "-p", "--port",
        type=int,
        help="Port where Local LLM server is hosted"
    )
        
    parser.add_argument(
        "--ref_doc",
        choices=['truncate', 'summarize', 'full'],
        default='truncate',
        help="Strategy to process reference documentation. Supported choices are:\
            \ntruncate    - Truncate documentation to the first paragraph\
            \nsummarize   - Generate a single summary of the documentation using the given LLM\
            \nfull        - Use the complete documentation (Can lead to very long context length)\
            \n\"truncate\" is used as the default strategy"
    )
    
    parser.add_argument(
        "--max_retries",
        type=int,
        default=3,
        help="Number of attempts that the LLM gets to generate the documentation for each function/method/class"
    )
    
    parser.add_argument(
        "--temperature",
        type=int,
        default=0.8,
        help="Temperature parameter used to sample output from the LLM"
    )
    
    parser.add_argument(
        "--max_tokens",
        type=int,
        default=2048,
        help="Maximum number of tokens that the LLM is allowed to generate"
    )

    args = parser.parse_args()
    verify_args(args)
    
    return args


def verify_args(args):
    parser = argparse.ArgumentParser()

    if not args.port and (not args.openai_key and not args.openai_key_env) and (not args.groq_key and not args.groq_key_env):
        raise parser.error('Use --port for a local LLM or --openai_key/--openai_key_env for openAI LLMs')
    
    if not args.port and (args.openai_model and not (args.openai_key or args.openai_key_env)):
        raise parser.error('One of --openai_key or --openai_key_env must be specified')

    if not args.port and (args.groq_model and not (args.groq_key or args.groq_key_env)):
        raise parser.error('One of --groq_key or --groq_key_env must be specified')


def generate_report(code_deps, report_path):
    data = []
    for k,v in code_deps.items():
        if v[CodeData.CUSTOM]:
            data.append({
                'path': v['path'],
                'function': k,
                'documentation': v[CodeData.DOC],
                'shortened documentation': v[CodeData.DOC_SHORT],                
                'code_before': v[CodeData.CODE],
                'code_after': v[CodeData.CODE_NEW]
            })
        
    pd.DataFrame(data).to_csv(report_path, index=False)


def get_code_dependancies_and_imports(path):
    import_stmts = []
    code_dependancies = CodeData()
    
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for file in files:
                if os.path.splitext(file)[-1] == '.py':
                    path = os.path.join(root, file)
                    logging.info(f'Extracting dependancies from {path}')
                    
                    with open(path) as f:    
                        code_str = f.read()
                        
                    import_stmts.extend(get_all_imports(code_str)[2])
                    get_all_calls(path, code_str, code_dependancies)
                    
    elif os.path.splitext(path)[-1] == '.py':
        logging.info(f'Extracting dependancies from {path}')
        
        with open(path) as f:    
            code_str = f.read()
        
        import_stmts.extend(get_all_imports(code_str)[2])
        get_all_calls(path, code_str, code_dependancies)
        
    else:
        raise Exception(f'Could not parse path: `{path}`')
    
    import_stmts = list(set(import_stmts))    
        
    return code_dependancies, import_stmts


def generate_documentation_for_custom_calls(code_dependancies, llm_mode, args):
    custom_funcs = [func_name for func_name, func_info in code_dependancies.items() if func_info[CodeData.CUSTOM]]

    num_custom_funcs = len(custom_funcs)
    num_digits = math.ceil(math.log(num_custom_funcs, 10))
    logging.info(f'Generating docs for {len(custom_funcs)} custom functions/methods/classes')

    total_tokens = TOK_COUNT.copy()

    for i in range(num_custom_funcs):
        least_dep_func = min(custom_funcs, key=lambda x: code_dependancies.undocumented_dependancies(x))
        reason = None
        
        for ri in range(args.max_retries):
            logging.debug(f'\tTry {ri+1}/{args.max_retries} for `{least_dep_func}`')
            llm_out, used_toks = get_llm_output(
                SYSTEM_PROMPT, 
                DOC_GENERATION_PROMPT(
                    code_dependancies[least_dep_func][CodeData.CODE], 
                    get_reference_docs_custom_functions(least_dep_func, code_dependancies)
                ),
                llm_mode,
                args,
            )
            total_tokens += used_toks
            
            new_func_code, new_func_node, success, reason = parse_commented_function(least_dep_func, llm_out)
            
            if not success:
                continue
        
            same, ast_reason = same_ast_with_reason(remove_docstring(code_dependancies[least_dep_func][CodeData.NODE]), remove_docstring(new_func_node))
            if same:
                code_dependancies.add(
                    least_dep_func,
                    {
                        CodeData.CODE_NEW: '\n'.join([code_dependancies[least_dep_func][CodeData.CODE_INDENT] + line for line in new_func_code.split('\n')]),
                        CodeData.DOC: ast.get_docstring(new_func_node),
                    }
                )
                logging.info(f'\t[{str(i+1).zfill(num_digits)}/{str(num_custom_funcs).zfill(num_digits)}] Generated docs for `{least_dep_func}` in {ri+1}/{args.max_retries} tries')      
                break
            else:
                # with open('debug.func.log', 'a') as f:
                #     print(f'func: {least_dep_func} | try: {ri}', file=f)
                #     print(new_func_code, file=f)
                #     print('-'*10, file=f)
                #     print(code_dependancies[least_dep_func][CodeData.CODE], file=f)
                #     print('-'*42, file=f)
                reason = f'AST mismatch: {ast_reason}'
        else:
            logging.info(f'\t[{str(i+1).zfill(num_digits)}/{str(num_custom_funcs).zfill(num_digits)}] Could not generate docs for `{least_dep_func}` after {args.max_retries} tries')
            logging.info(f'\t\tReason: {reason}')
        
        if code_dependancies[least_dep_func][CodeData.DOC] != '-':
            code_dependancies.add(
                least_dep_func, 
                {CodeData.DOC_SHORT: get_shortened_docs(least_dep_func, CodeData.DOC, args.ref_doc, llm_mode, args)}
            )            

        custom_funcs.remove(least_dep_func)
        
    custom_funcs_with_docs = [func_name for func_name, func_info in code_dependancies.items() if func_info[CodeData.CUSTOM] and func_info[CodeData.DOC] != '-']
    logging.info(f'Generated docs for {len(custom_funcs_with_docs)}/{num_custom_funcs} custom functions/classes.methods')
    logging.info(f'Tokens used: ' + ', '.join(f'{k}: {v}' for k,v in total_tokens.items()))
    
    
def replace_modified_functions(code_dependancies, path):
    custom_funcs_with_docs = [func_name for func_name, func_info in code_dependancies.items() if func_info[CodeData.CUSTOM] and func_info[CodeData.DOC] != '-']
    
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for file in files:
                if os.path.splitext(file)[-1] == '.py':
                    path = os.path.join(root, file)
                    logging.info(f'Replacing functions in {path}')
                    
                    with open(path) as f:    
                        file_str = f.read()
                        
                    changed = False
                    path_funcs = sorted(
                        [func for func in custom_funcs_with_docs if code_dependancies[func][CodeData.PATH] == path]
                        , key = lambda x: 1 if code_dependancies[func][CodeData.TYPE] == 'class' else 0
                    )
                    for func in path_funcs:
                        if code_dependancies[func][CodeData.PATH] == path:
                            changed = True
                            file_str = replace_func(
                                            func, 
                                            code_dependancies[func][CodeData.CODE], 
                                            code_dependancies[func][CodeData.CODE_NEW], 
                                            path,
                                            file_str
                                        )
                            
                    if changed:
                        with open(path, 'w') as f:
                            f.write(file_str)
                    
    elif os.path.splitext(path)[-1] == '.py':
        
        logging.info(f'Replacing functions in {path}')
        
        with open(path) as f:    
            file_str = f.read()
        
        changed = False
        for func in custom_funcs_with_docs:
            if code_dependancies[func][CodeData.PATH] == path:
                changed = True
                file_str = replace_func(
                                func, 
                                code_dependancies[func][CodeData.CODE], 
                                code_dependancies[func][CodeData.CODE_NEW], 
                                path,
                                file_str
                            )
                
        if changed:
            with open(path, 'w') as f:
                f.write(file_str)
        
    else:
        raise Exception(f'Could not parse path: `{path}`')