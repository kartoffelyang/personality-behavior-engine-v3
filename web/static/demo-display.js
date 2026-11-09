(function(root){
'use strict';
function text(value){
 if(typeof value!=='string')return value;
 return value
  .replace(/(?:按照|根据|基于|按|依照)(?:本轮|当前|共同|共享|给定|内部)?(?:的)?(?:参考设定|演示假设|演示设定|虚拟设定|模拟设定|设定)(?:来看|来说)?[，,:：、\s]*/g,'')
  .replace(/参考设定[，,:：、\s]*/g,'')
  .replace(/本轮演示信息/g,'参考信息')
  .replace(/(?:共同的演示设定|共同虚拟设定)/g,'共同设定')
  .replace(/演示假设/g,'参考信息')
  .replace(/强化演示/g,'强化对比')
  .replace(/，不表示车辆已到达或已停车[。]?/g,'。')
  .replace(/[（(](?:共同|共享|本轮)?(?:虚拟|模拟|演示)(?:地点|内容|候选|资源|假设|数据)?[）)]/g,'')
  .replace(/虚拟|模拟|演示/g,'');
}
function apply(rootNode){
 const doc=rootNode.ownerDocument||rootNode,walker=doc.createTreeWalker(rootNode,4);
 let node;while((node=walker.nextNode())){
  if(node.parentElement?.closest('script,style,pre,code,textarea'))continue;
  const value=text(node.nodeValue);if(value!==node.nodeValue)node.nodeValue=value;
 }
}
const api={text,apply};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DemoDisplay=api;
})(typeof window==='undefined'?{}:window);
