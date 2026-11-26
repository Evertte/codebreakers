let python;
let queue=Promise.resolve();
self.onmessage=event=>{
  queue=queue.then(async()=>{
    if(!python){
      importScripts('pyodide/pyodide.js');
      python=await loadPyodide({indexURL:new URL('pyodide/',self.location.href).href});
      const source=await fetch('course_runtime.py').then(r=>{if(!r.ok)throw Error('Python lesson runtime could not load');return r.text();});
      // Keep one module namespace for the generator between action acknowledgements.
      python.globals.set('_course_source',source);
      python.runPython("import types\n_course = types.ModuleType('course')\nexec(_course_source, _course.__dict__)");
    }
    python.globals.set('_message',event.data);
    self.postMessage(python.runPython('_course.handle(_message)'));
  }).catch(error=>self.postMessage(JSON.stringify({kind:'error',message:String(error),line:1})));
};
