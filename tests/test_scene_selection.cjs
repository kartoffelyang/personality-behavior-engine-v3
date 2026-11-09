const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
test('selecting or editing a scene never starts a request',()=>{
 const elements=new Map();function element(){return {textContent:'',hidden:false,disabled:false,value:'',listeners:{},addEventListener(k,f){this.listeners[k]=f},setAttribute(){},append(){},replaceChildren(...c){this.children=c}}}
 function get(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)}
 get('model-context').textContent=JSON.stringify({backend:'kev',path:'/demo/kev',label:'KEV'});
 get('scenario-metadata').textContent=JSON.stringify([{id:'case1',why_suitable:'有取舍',observe:'采用差异'}]);
 const button=element();button.dataset={case:'case1',text:'车停好了，想听音乐'};let calls=0;
 const context={document:{getElementById:get,createElement:element,querySelectorAll:s=>s==='[data-case]'?[button]:[]},DemoFactors:{},DemoContrast:{},DemoDisplay:{text:require('../web/static/demo-display.js').text,apply(){}},URLSearchParams,location:{search:''},sessionStorage:{getItem(){return null},setItem(){}},history:{replaceState(){}},clearTimeout,setTimeout,fetch(){calls++;throw Error('unexpected request')}};
 vm.runInNewContext(fs.readFileSync('web/static/persona-demo.js','utf8'),context);
 button.listeners.click();assert.equal(calls,0);assert.equal(get('scene').value,button.dataset.text);assert.match(get('generate').textContent,/开始对比/);
 get('scene').listeners.input();assert.equal(calls,0);
});
