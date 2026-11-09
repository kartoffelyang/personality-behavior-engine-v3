const {test}=require('node:test');const assert=require('node:assert/strict');
const {compared}=require('../web/static/kev-atomic.js');
test('probability differences do not masquerade as adopted action differences',()=>{
 const scope={rows:[{id:'x'}]},variants={ESTJ:{atomic_scores:[{id:'x',probability:.6,adopted:true}]},INFP:{atomic_scores:[{id:'x',probability:.9,adopted:true}]}};
 const [r]=compared(scope,variants);assert.equal(r.differs,false);assert.ok(Math.abs(r.delta+.3)<1e-9);
});
test('dependency actions retain missing probabilities, and partial output has no invented peer',()=>{
 const scope={rows:[{id:'x'}]},variants={ESTJ:{atomic_scores:[{id:'x',probability:null,adopted:true}]}};
 const [r]=compared(scope,variants);assert.equal(r.delta,null);assert.equal(r.differs,false);assert.equal(r.right,undefined);
});
