import logging
from prompts import SYSTEM_PROMPT, DOC_SUMMARIZATION_PROMPT
from llm_inference import get_llm_output

class CodeData:
    
    DEP = 'dependances'
    DOC = 'documentation'
    DOC_SHORT = 'documentation_short'
    CODE = 'code'
    CODE_NEW = 'code_new'
    CODE_INDENT = 'code_indent'
    NODE = 'node'
    CUSTOM = 'custom'
    PATH = 'path'
    TYPE = 'code_type'
    
    def __init__(self):
        self.code_blobs = {}
        self.DEFAULT_OUTPUT = {
            CodeData.DEP: [], 
            CodeData.DOC: '-',
            CodeData.DOC_SHORT: '-',
            CodeData.NODE: None, 
            CodeData.CODE: '-',
            CodeData.CODE_NEW: '-',
            CodeData.CUSTOM: False,
            CodeData.PATH: '-',
            CodeData.CODE_INDENT: '',
            CodeData.TYPE: '??',
        }
        
    def __getitem__(self, name):
        return self.code_blobs.get(name, self.DEFAULT_OUTPUT.copy())
    
    def add(self, name, data):
        if name not in self.code_blobs:
            self.code_blobs[name] = self.DEFAULT_OUTPUT.copy()
            
        for k,v in data.items():        
            if k == CodeData.DEP:
                self.code_blobs[name][k] = self.code_blobs[name].get(k, []) + v
                for func in v:
                    self.add(func, {CodeData.DEP: [], CodeData.PATH: data.get(CodeData.PATH, '-')})
            else:
                self.code_blobs[name][k] = v
                 
    def dependancies(self, name):
        fobj = self.code_blobs.get(name, {})
        return len(fobj.get(CodeData.DEP, []))
    
    def documented_dependancies(self, name):
        fobj = self.code_blobs.get(name, {})
        return len([f for f in fobj.get(CodeData.DEP, []) if self.__getitem__(f)[CodeData.DOC] != '-'])
            
    def undocumented_dependancies(self, name):
        fobj = self.code_blobs.get(name, {})
        return len([f for f in fobj.get(CodeData.DEP, []) if not self.__getitem__(f)[CodeData.DOC] == '-'])

    def items(self):
        return self.code_blobs.items()

    def keys(self):
        return self.code_blobs.keys()

    def values(self):
        return self.code_blobs.values()
    
    def __str__(self):
        
        custom_funcs = [(func, func_info) for func,func_info in self.code_blobs.items() if func_info[CodeData.CUSTOM]]
        ref_funcs = [(func, func_info) for func,func_info in self.code_blobs.items() if not func_info[CodeData.CUSTOM]]
        
        out_str = ''
        out_str += f'Custom ({len(custom_funcs)}):\n'
        out_str += '-'*12 + '\n'
        for func,func_info in custom_funcs:
            out_str += f'{func_info[CodeData.TYPE]:<10}: `{func}` Dependancies: {self.dependancies(func)}, Documented Dependancies: {self.documented_dependancies(func)}\n'
        out_str += f'\nReference ({len(ref_funcs)}):\n'
        out_str += '-'*15 + '\n'
        out_str += ', '.join([f'`{func}`' for func,_ in ref_funcs]) + '\n'

        return out_str

    def __repr__(self):
        return self.__str__()


def clean_doc_str(doc_str):
    doc_str = doc_str.strip()
    doc_str = doc_str.lstrip('\n')
    doc_str = doc_str.rstrip('\n')
    return doc_str
    
    
def get_reference_docs_simple_functions(import_stmts, funcs):
    for stmt in import_stmts:
        try:
            exec(stmt)
        except ImportError:
            logging.debug(f'Could not import using the statement: `{stmt}`')
            
    docs = []
    for func in funcs:
        func_doc = '-'
        func_parts = func.split('.')
        for i in range(len(func_parts)):
            subfunc = '.'.join(func_parts[i:])
            try:
                func_doc = clean_doc_str(eval(f'{subfunc}.__doc__'))
                break
            except:
                pass
        
        docs.append(func_doc)
        if func_doc == '-':
            logging.debug(f'No reference documentation found for func: {func}')
    
    return docs


def get_reference_docs_custom_functions(func, code_dependancies):
    ref_docs = []
    for dep_func in code_dependancies[func][CodeData.DEP]:
        if code_dependancies[dep_func][CodeData.DOC_SHORT] != '-':
            ref_docs.append({
                'function': dep_func,
                'doc_str': code_dependancies[dep_func][CodeData.DOC_SHORT]
            })
    return ref_docs


def get_summarized_docs(func_name, doc_str, mode, args):
   return get_llm_output(SYSTEM_PROMPT, DOC_SUMMARIZATION_PROMPT(func_name, doc_str), mode, args)


def get_truncated_docs(func_name, doc_str):
    trunc_doc_str = doc_str.split('\n\n')[0]
    if len(trunc_doc_str) == len(doc_str):
        trunc_doc_str = doc_str.split('\n')[0]
        
    logging.debug(f'Truncated doc for {func_name} from {len(doc_str)} to {len(trunc_doc_str)}')
        
    return trunc_doc_str


def get_shortened_docs(func_name, doc_str, mode, llm_mode, args):
    if not doc_str or doc_str == '-':
        return doc_str

    if mode == 'summarize':
        return get_summarized_docs(func_name, doc_str, llm_mode, args)
    elif mode == 'truncate':
        return get_truncated_docs(func_name, doc_str)
    elif mode == 'full':
        return doc_str
    else:
        logging.warning(f'Could not shorten doc for `{func_name}` using mode: `{mode}`, using truncation')
        return get_truncated_docs(func_name, doc_str)