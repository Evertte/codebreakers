"""CODEBREAKERS: restricted, instrumented Python executed by CPython/Pyodide.
Assignments retain Python semantics. Only explicit API calls yield combat commands.
"""
import ast
import json
import math
import operator

COMMANDS = {'move_left': 1, 'move_right': 1, 'move_toward': 1, 'move_away': 1,
            'punch': 0, 'kick': 0, 'block': 1, 'wait': 1}
FIELDS = {'enemy': {'health', 'distance', 'is_attacking', 'is_blocking', 'is_recovering'},
          'fighter': {'health'}}
LESSON_FIELDS = {'loops': {'exchanges_left'}, 'functions': {'target_distance'},
                 'classes': {'style', 'step_hint', 'exchanges_left'}}
ALLOWED = (ast.Module, ast.Assign, ast.AugAssign, ast.Expr, ast.If, ast.For, ast.While,
           ast.FunctionDef, ast.Return, ast.arguments, ast.arg,
           ast.Pass, ast.Break, ast.Continue, ast.Name, ast.Load, ast.Store, ast.Constant,
           ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Attribute,
           ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
           ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq,
           ast.Lt, ast.LtE, ast.Gt, ast.GtE)

class LessonError(Exception):
    pass

def validate_function(node, method, reserved):
    if (node.name.startswith('_') and not (method and node.name=='__init__')) or node.name in reserved:
        raise LessonError('Choose a different function or method name.')
    args=node.args
    if (node.decorator_list or node.returns or getattr(node,'type_params',[]) or args.posonlyargs
            or args.kwonlyargs or args.vararg or args.kwarg or args.defaults or args.kw_defaults):
        raise LessonError('Use simple definitions with positional parameters.')
    names=[arg.arg for arg in args.args]
    if method and (not names or names[0]!='self'):
        raise LessonError('Start each method’s parameters with self.')
    if len(names)>3+int(method) or len(set(names))!=len(names) or any(name.startswith('_') or name in reserved for name in names):
        raise LessonError('Use up to three distinct parameter names.')
    if node.name=='__init__':
        for statement in node.body:
            if isinstance(statement,ast.Pass):
                continue
            if not (isinstance(statement,ast.Assign) and len(statement.targets)==1
                    and isinstance(statement.targets[0],ast.Attribute)
                    and isinstance(statement.targets[0].value,ast.Name) and statement.targets[0].value.id=='self'
                    and (isinstance(statement.value,ast.Constant) or
                         isinstance(statement.value,ast.Name) and statement.value.id in names[1:])):
                raise LessonError('In __init__, store a parameter or literal in a self field. Put actions in a separate method.')

class Actor:
    def __init__(self, runtime, name):
        self.runtime, self.name = runtime, name
    def __getattr__(self, field):
        self.runtime.reads += 1
        value=self.runtime.state.get(self.name, {}).get(field, 0)
        self.runtime.observed.append({'name':f'{self.name}.{field}','value':repr(value)})
        if self.runtime.capture_stack:
            self.runtime.capture_stack[-1][f'{self.name}.{field}']=repr(value)
        return value

class Instrument(ast.NodeTransformer):
    def __init__(self, functions, classes, lesson):
        self.functions = functions
        self.classes = classes
        self.trace_actions = lesson in ('expressions', 'ninja-challenge', 'functions', 'classes')
        self.trace_decisions = self.trace_actions or lesson in ('conditions', 'loops')
    def call(self, name, args):
        return ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=args, keywords=[])
    def body(self, body):
        result=[]
        for node in body:
            line=node.lineno
            result.append(ast.Expr(value=ast.Yield(value=self.call('_step', [ast.Constant(line),self.call('locals',[])]))))
            result.append(self.visit(node))
        return result
    def visit_If(self, node):
        expression=ast.unparse(node.test) if hasattr(ast, 'unparse') else 'condition'
        if self.trace_decisions:
            node.test=self.call('_decision',[self.call('_capture',[]),self.visit(node.test),ast.Constant(node.lineno),ast.Constant(expression)])
            node.body=self.branch(node.body)
            node.orelse=self.branch(node.orelse) if node.orelse else []
            return node
        node.test=self.call('_test',[self.visit(node.test),ast.Constant(node.lineno),ast.Constant(expression)])
        node.body=self.body(node.body);node.orelse=self.body(node.orelse)
        return node
    def branch(self, body):
        return [ast.Expr(value=self.call('_enter_branch',[])),
                ast.Try(body=self.body(body),handlers=[],orelse=[],
                        finalbody=[ast.Expr(value=self.call('_leave_branch',[]))])]
    def source_scope(self):
        return ast.Name(id='_sources',ctx=ast.Load())
    def init_sources(self):
        return ast.Assign(targets=[ast.Name(id='_sources',ctx=ast.Store())],value=ast.Dict(keys=[],values=[]))
    def visit_Name(self, node):
        if self.trace_decisions and isinstance(node.ctx,ast.Load):
            return ast.copy_location(self.call('_read_source',[self.source_scope(),ast.Constant(node.id),node]),node)
        return node
    def visit_Assign(self, node):
        value=self.visit(node.value)
        if isinstance(node.targets[0], ast.Attribute):
            target=node.targets[0]
            return ast.copy_location(ast.Expr(value=self.call('_write_field',[
                target.value,ast.Constant(target.attr),value])),node)
        if self.trace_decisions:
            names=ast.Tuple(elts=[ast.Constant(target.id) for target in node.targets],ctx=ast.Load())
            value=self.call('_save_sources',[self.call('_capture',[]),value,self.source_scope(),names])
        node.value=value
        return node
    def visit_FunctionDef(self, node):
        node.body=self.body(node.body)
        if self.trace_decisions:
            source_init=self.init_sources()
            if self.trace_actions:
                names=[arg.arg for arg in node.args.args if arg.arg != 'self']
                source_init.value=self.call('_parameter_sources',[ast.Constant(node.name),
                    ast.Tuple(elts=[ast.Constant(name) for name in names],ctx=ast.Load()),
                    ast.Tuple(elts=[ast.Name(id=name,ctx=ast.Load()) for name in names],ctx=ast.Load())])
            node.body.insert(0,source_init)
        return node
    def visit_ClassDef(self, node):
        # Constructors are ordinary Python methods. Combat methods may suspend at
        # a fighter action, just like ordinary learner functions.
        node.body=[method if method.name=='__init__' else self.visit(method) for method in node.body]
        return node
    def visit_Attribute(self, node):
        if self.classes and isinstance(node.ctx,ast.Load) and node.value.id not in FIELDS:
            return ast.copy_location(self.call('_read_field',[node.value,ast.Constant(node.attr)]),node)
        return node
    def visit_Return(self, node):
        if node.value is not None:
            value=self.visit(node.value)
            node.value=self.call('_return_sources',[self.call('_capture',[]),value]) if self.trace_actions else value
        return node
    def visit_While(self,node):
        expression=ast.unparse(node.test) if hasattr(ast, 'unparse') else 'condition'
        test=[self.visit(node.test),ast.Constant(node.lineno),ast.Constant(expression)]
        node.test=self.call('_decision',[self.call('_capture',[]),*test]) if self.trace_decisions else self.call('_test',test)
        node.body=self.loop_body(node.body,'while');node.orelse=self.body(node.orelse);return node
    def visit_For(self,node):
        node.iter=self.visit(node.iter);node.body=self.loop_body(node.body,'for');node.orelse=self.body(node.orelse);return node
    def loop_body(self,body,kind):
        # try/finally ensures break and continue restore the instrumentation depth.
        return [ast.Expr(value=self.call('_enter',[ast.Constant(kind)])),ast.Try(body=self.body(body),handlers=[],orelse=[],finalbody=[ast.Expr(value=self.call('_leave',[ast.Constant(kind)]))])]
    def visit_BinOp(self,node):
        return self.call('_binary',[ast.Constant(type(node.op).__name__),self.visit(node.left),self.visit(node.right)])
    def visit_AugAssign(self,node):
        return self.visit_Assign(ast.Assign(targets=[node.target],value=self.call('_binary',[
            ast.Constant(type(node.op).__name__),ast.Name(id=node.target.id,ctx=ast.Load()),node.value])))
    def visit_Call(self,node):
        if isinstance(node.func,ast.Attribute) and node.func.value.id != 'fighter':
            args=ast.List(elts=[self.visit(a) for a in node.args],ctx=ast.Load())
            return ast.copy_location(ast.YieldFrom(value=self.call('_invoke_profile',[
                node.func.value,ast.Constant(node.func.attr),args])),node)
        if isinstance(node.func,ast.Attribute):
            refs=sorted({n.id for arg in node.args for n in ast.walk(arg) if isinstance(n,ast.Name) and n.id not in ('fighter','enemy')})
            args=ast.List(elts=[self.visit(a) for a in node.args],ctx=ast.Load())
            arguments=[ast.Constant(node.func.attr),args,ast.Constant(node.lineno),ast.Constant(','.join(refs)),self.call('locals',[])]
            action=self.call('_tracked_action',[self.call('_capture',[]),*arguments]) if self.trace_actions else self.call('_action',arguments)
            return ast.copy_location(ast.Yield(value=action),node)
        if isinstance(node.func,ast.Name) and node.func.id in self.classes:
            args=ast.List(elts=[self.visit(a) for a in node.args],ctx=ast.Load())
            return ast.copy_location(self.call('_make_profile',[node.func,args]),node)
        if isinstance(node.func,ast.Name) and node.func.id in self.functions:
            args=ast.List(elts=[self.visit(a) for a in node.args],ctx=ast.Load())
            return ast.copy_location(ast.YieldFrom(value=self.call('_invoke',[ast.Constant(node.func.id),ast.Name(id=node.func.id,ctx=ast.Load()),args])),node)
        return self.generic_visit(node)

class CourseRuntime:
    def __init__(self):
        self.generator=None;self.state={};self.steps=0;self.depth=0;self.while_depth=0;self.reads=0;self.tests=0;self.line=1;self.output=[];self.variables=[];self.arithmetic=False
        self.function_calls=0;self.function_calls_with_args=0;self.function_arguments=set()
        self.observed=[];self.last_condition='';self.last_condition_line=0;self.last_condition_result=False;self.last_condition_values=[]
        self.capture_stack=[];self.decisions=[];self.branches=[];self.last_decision=None
        self.call_stack=[];self.profiles={}
    def capture(self):
        observed={}
        self.capture_stack.append(observed)
        return observed
    def read_source(self, scope, name, value):
        if self.capture_stack:
            self.capture_stack[-1].update(scope.get(name, {}))
        return value
    def save_sources(self, captured, value, scope, names):
        self.capture_stack.pop()
        for name in names:
            scope[name]=captured
        return value
    def parameter_sources(self, function, names, values):
        return {name:{f'param|{function}|{name}':repr(value)} for name,value in zip(names,values)}
    def return_sources(self, captured, value):
        self.capture_stack.pop()
        if self.capture_stack and self.call_stack:
            self.capture_stack[-1].update(captured)
            self.capture_stack[-1][f'return|{self.call_stack[-1]}']=repr(value)
        return value
    def tracked_action(self, captured, *args):
        self.capture_stack.pop()
        event=self.action(*args)
        event['actionSources']=[{'name':key,'value':value} for key,value in captured.items()]
        event['actionFunction']=self.call_stack[-1] if self.call_stack else ''
        return event
    def make_profile(self, cls, args):
        if not all(self.is_scalar(value) for value in args):
            raise LessonError('Use numbers, text, or Booleans to create a profile.')
        profile=cls(*args)
        self.profiles[id(profile)]=profile
        return profile
    def require_profile(self, profile):
        if id(profile) not in self.profiles:
            raise LessonError('Use an object created from your own class.')
    def read_field(self, profile, name):
        self.require_profile(profile)
        value=getattr(profile,name)
        if not self.is_scalar(value):
            raise LessonError('Read a stored profile value or call a method with parentheses.')
        if self.capture_stack:
            self.capture_stack[-1][f'field|{id(profile)}|{name}']=repr(value)
        return value
    def write_field(self, profile, name, value):
        self.require_profile(profile)
        if not self.is_scalar(value) or hasattr(type(profile),name):
            raise LessonError('Store a number, text, or a Boolean in a profile field.')
        setattr(profile,name,value)
    @staticmethod
    def is_scalar(value):
        return isinstance(value,(str,int,float,bool,type(None)))
    def invoke_profile(self, profile, name, args):
        self.require_profile(profile)
        fn=type(profile).__dict__.get(name)
        if not callable(fn):
            raise LessonError('Call a method defined on your class.')
        return self.invoke(name,fn,[profile,*args])
    def decision(self, captured, value, line, expression):
        self.capture_stack.pop()
        result=self.test(value,line,expression)
        self.last_decision={'line':line,'expression':expression,'result':result,
                            'observations':[{'name':name,'value':observed} for name,observed in captured.items()]}
        self.decisions.append(self.last_decision)
        self.decisions=self.decisions[-16:]
        return result
    def enter_branch(self):
        self.branches.append(self.last_decision)
    def leave_branch(self):
        self.branches.pop()
    def decision_events(self):
        decisions=self.decisions
        self.decisions=[]
        return {'decisions':decisions,'activeBranches':self.branches[:]}
    def variables_at(self,scope):
        return [{'name':key,'value':repr(value)} for key,value in scope.items() if not key.startswith('_') and key not in ('fighter','enemy') and isinstance(value,(str,int,float,bool,type(None)))][:40]
    def step(self,line,scope):
        self.steps+=1;self.line=line;self.variables=self.variables_at(scope)
        if self.steps>2000:raise LessonError('Execution limit reached. Check your loop condition; your program has been stopped.')
        return {'kind':'step','line':line,'variables':self.variables}
    def test(self,value,line,expression):
        # Attribute reads have already happened exactly once under normal Python
        # evaluation (including short-circuiting) before this wrapper is called.
        self.tests+=1;self.line=line;self.last_condition=expression;self.last_condition_line=line
        self.last_condition_result=bool(value);self.last_condition_values=self.observed[:];self.observed.clear()
        return self.last_condition_result
    def enter(self,kind):
        self.depth+=1
        if kind=='while':self.while_depth+=1
    def leave(self,kind):
        self.depth-=1
        if kind=='while':self.while_depth-=1
    def action(self,name,args,line,refs,scope):
        self.line=line
        if len(args)!=COMMANDS[name]:raise TypeError(f'fighter.{name}() takes {COMMANDS[name]} argument(s)')
        if args and (isinstance(args[0],bool) or not isinstance(args[0],(int,float))):raise TypeError(f'fighter.{name}() needs a number')
        if args and (not math.isfinite(args[0]) or not 0<args[0]<=10):raise ValueError('Distance and duration must be greater than 0 and at most 10')
        self.variables=self.variables_at(scope)
        return {'kind':'action','command':name,'amount':args[0] if args else 0,'line':line,'references':refs,'loopDepth':self.depth,'whileDepth':self.while_depth,'tests':self.tests,'reads':self.reads,'arithmetic':self.arithmetic,'variables':self.variables,'functionCalls':self.function_calls,'functionCallsWithArgs':self.function_calls_with_args,'distinctFunctionArguments':len(self.function_arguments),'condition':self.last_condition,'conditionLine':self.last_condition_line,'conditionResult':self.last_condition_result,'observedValues':self.last_condition_values,**self.decision_events()}
    def invoke(self,name,fn,args):
        self.function_calls+=1
        if self.function_calls>1000:raise LessonError('Too many function calls. Check where you call your function.')
        if args:
            self.function_calls_with_args+=1
            self.function_arguments.add((name,tuple(repr(value) for value in args)))
        self.call_stack.append(name)
        try:
            return (yield from fn(*args))
        finally:
            self.call_stack.pop()
    def binary(self,op,a,b):
        self.arithmetic=True
        if op=='Mult' and (isinstance(a,str) or isinstance(b,str)):
            size=len(a) if isinstance(a,str) else len(b);count=b if isinstance(a,str) else a
            if not isinstance(count,int) or size*max(0,count)>2048:raise LessonError('Text result is too large for this lesson.')
        result={'Add':operator.add,'Sub':operator.sub,'Mult':operator.mul,'Div':operator.truediv,'FloorDiv':operator.floordiv,'Mod':operator.mod}[op](a,b)
        if self.capture_stack and isinstance(result,(int,float)):
            self.capture_stack[-1]['arithmetic']=repr(result)
        if isinstance(result,str) and len(result)>2048:raise LessonError('Text result is too large for this lesson.')
        if isinstance(result,(int,float)) and (not math.isfinite(result) or abs(result)>1000000):raise LessonError('Number is outside the lesson limit.')
        return result
    def safe_range(self,*args):
        value=range(*args)
        if len(value)>1000:raise LessonError('Use at most 1,000 iterations in range().')
        return value
    def print_value(self,*args):
        self.output.append(' '.join(str(a) for a in args)[:2048]);self.output=self.output[-10:]
    def start(self,source,state,lesson=''):
        self.__init__();self.state=state
        try:
            if len(source)>8000:raise LessonError('Keep this program under 8,001 characters.')
            tree=ast.parse(source,filename='your_code.py')
            if lesson=='loops' and any(isinstance(node,ast.For) for node in ast.walk(tree)):
                raise LessonError('This lesson practices while loops. Use while with a condition that can change during the fight.')
            if len(list(ast.walk(tree)))>1500:raise LessonError('This program is too large for a lesson.')
            functions={node.name for node in tree.body if isinstance(node,ast.FunctionDef)}
            classes={node.name for node in tree.body if isinstance(node,ast.ClassDef)} if lesson=='classes' else set()
            if len(functions)>8 or len(classes)>4:
                raise LessonError('Use at most eight functions and four classes in a program.')
            reserved={'fighter','enemy','range','print'}
            class_methods=[]
            for node in tree.body:
                if isinstance(node,ast.ClassDef) and node.name in classes:
                    if node.name.startswith('_') or node.name in reserved | functions:
                        raise LessonError('Choose a distinct name for your class.')
                    if node.bases or node.keywords or node.decorator_list or getattr(node,'type_params',[]):
                        raise LessonError('Use a plain class without inheritance or decorators in this lesson.')
                    if not node.body or not all(isinstance(method,ast.FunctionDef) for method in node.body):
                        raise LessonError('Put methods inside the class and store values in __init__.')
                    class_methods.extend(node.body)
                if isinstance(node,ast.FunctionDef):
                    validate_function(node,False,reserved | classes)
            for method in class_methods:
                validate_function(method,True,reserved)
            for node in ast.walk(tree):
                self.line=getattr(node,'lineno',self.line)
                if not isinstance(node,ALLOWED) and not (isinstance(node,ast.ClassDef) and node.name in classes and node in tree.body):
                    raise LessonError(f'{type(node).__name__} is not available in these lessons yet.')
                if isinstance(node,ast.FunctionDef) and node not in tree.body and node not in class_methods:
                    raise LessonError('Define functions at the top or methods directly inside your class.')
                if isinstance(node,ast.Name):
                    if node.id.startswith('_'):
                        raise LessonError('Names starting with _ are reserved.')
                    if isinstance(node.ctx,ast.Store) and node.id in reserved | functions | classes:
                        raise LessonError(f'{node.id} is already used; choose another variable name.')
                if isinstance(node,ast.Assign):
                    simple=all(isinstance(t,ast.Name) for t in node.targets)
                    field=len(node.targets)==1 and isinstance(node.targets[0],ast.Attribute) and classes
                    if not simple and not field:
                        raise LessonError('Assign to a variable or a field on your own object.')
                if isinstance(node,ast.AugAssign) and not isinstance(node.target,ast.Name):
                    raise LessonError('To change a field, use assignment, such as self.step = self.step + 1.')
                if isinstance(node,ast.Constant) and (not isinstance(node.value,(str,int,float,bool,type(None))) or isinstance(node.value,str) and len(node.value)>2048 or isinstance(node.value,(int,float)) and abs(node.value)>1000000):
                    raise LessonError('Value is outside the lesson limits.')
                if isinstance(node,ast.Attribute):
                    if not isinstance(node.value,ast.Name) or node.attr.startswith('_'):
                        raise LessonError('Use a documented observation or a public field on an object.')
                    owner=node.value.id
                    if owner in FIELDS:
                        allowed=FIELDS[owner] | (set(COMMANDS) if owner=='fighter' else LESSON_FIELDS.get(lesson,set()))
                        if node.attr not in allowed or not isinstance(node.ctx,ast.Load):
                            raise LessonError('Use a documented fighter command or read-only observation.')
                    elif not classes:
                        raise LessonError('Use a documented fighter command or enemy observation.')
                if isinstance(node,ast.Call):
                    if node.keywords:
                        raise LessonError('Use positional arguments in these lessons.')
                    if isinstance(node.func,ast.Name):
                        if node.func.id not in {'range','print'} | functions | classes:
                            raise LessonError('Call a function or class you defined or use a documented fighter command.')
                    elif isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name):
                        owner=node.func.value.id
                        if not (owner=='fighter' and node.func.attr in COMMANDS or owner not in FIELDS and classes):
                            raise LessonError('Use a documented fighter command or a method on your own object.')
                    else:
                        raise LessonError('Use a named function, method, or fighter command.')
            transform=Instrument(functions,classes,lesson)
            body=transform.body(tree.body)
            if transform.trace_decisions:
                body.insert(0,transform.init_sources())
            body.append(ast.Expr(value=ast.Yield(value=transform.call('_step',[ast.Constant(max(1,len(source.splitlines()))),transform.call('locals',[])]))))
            body.append(ast.Expr(value=ast.Yield(value=ast.Constant(None))))
            fn=ast.FunctionDef(name='_program',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body,decorator_list=[])
            module=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))
            env={'__builtins__':{'__build_class__':__build_class__} if classes else {},'__name__':'lesson','fighter':Actor(self,'fighter'),'enemy':Actor(self,'enemy'),'range':self.safe_range,'print':self.print_value,'locals':locals,
                 '_step':self.step,'_test':self.test,'_enter':self.enter,'_leave':self.leave,'_action':self.action,'_binary':self.binary,'_invoke':self.invoke,
                 '_capture':self.capture,'_read_source':self.read_source,'_save_sources':self.save_sources,'_decision':self.decision,
                 '_enter_branch':self.enter_branch,'_leave_branch':self.leave_branch,
                 '_parameter_sources':self.parameter_sources,'_return_sources':self.return_sources,'_tracked_action':self.tracked_action,
                 '_make_profile':self.make_profile,'_read_field':self.read_field,'_write_field':self.write_field,
                 '_invoke_profile':self.invoke_profile}
            exec(compile(module,'your_code.py','exec'),env)
            self.generator=env['_program']();return self.advance(state)
        except Exception as error:return self.error(error)
    def error(self,error):
        self.generator=None
        return {'kind':'error','line':getattr(error,'lineno',None) or self.line,'message':f'{type(error).__name__}: {error}','variables':self.variables}
    def advance(self,state):
        self.state=state
        try:
            while self.generator is not None:
                event=next(self.generator)
                if event is None:self.generator=None;break
                if event['kind']=='action':event['output']='\n'.join(self.output);return event
            return {'kind':'done','line':self.line,'variables':self.variables,'output':'\n'.join(self.output),'functionCalls':self.function_calls,'functionCallsWithArgs':self.function_calls_with_args,'distinctFunctionArguments':len(self.function_arguments),**self.decision_events()}
        except StopIteration:
            self.generator=None;return {'kind':'done','line':self.line,'variables':self.variables,'output':'\n'.join(self.output),'functionCalls':self.function_calls,'functionCallsWithArgs':self.function_calls_with_args,'distinctFunctionArguments':len(self.function_arguments),**self.decision_events()}
        except Exception as error:return self.error(error)

_runtime=CourseRuntime()
def handle(message):
    data=json.loads(message)
    result=_runtime.start(data['source'],data['state'],data.get('lesson','')) if data['kind']=='start' else _runtime.advance(data['state'])
    return json.dumps(result)

if __name__=='__main__':
    import sys
    for message in sys.stdin:
        try:print(handle(message),flush=True)
        except Exception as error:print(json.dumps({'kind':'error','message':str(error),'line':1}),flush=True)
