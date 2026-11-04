const fs=require('node:fs'),path=require('node:path');
// This queue stores observations, never a submission grant or replayable command.
class ReviewQueue {
  constructor(directory,now=()=>Date.now()) {
    this.directory=directory;this.now=now;this.file=path.join(directory,'review-queue.json');
    this.items=fs.existsSync(this.file)?JSON.parse(fs.readFileSync(this.file,'utf8')):[];
    for(const item of this.items)if(item.status==='running')item.status='scheduled';
  }
  save(){fs.mkdirSync(this.directory,{recursive:true});fs.writeFileSync(this.file,JSON.stringify(this.items,null,2));}
  enqueue(checkpoint) {
    if(!checkpoint.orderBinding)return;
    this.items=this.items.filter(i=>i.binding!==checkpoint.orderBinding);
    if(!['waiting-pcb-review','waiting-smt-review'].includes(checkpoint.result?.phase)){this.save();return;}
    const item={binding:checkpoint.orderBinding,projectId:checkpoint.projectId,projectVersion:checkpoint.projectVersion,
      quantity:checkpoint.quantity,phase:checkpoint.result.phase,status:'scheduled',nextCheckAt:this.now()+60000};
    this.items=this.items.filter(i=>i.binding!==item.binding);this.items.push(item);this.save();
  }
  due(){return this.items.find(i=>i.status==='scheduled'&&i.nextCheckAt<=this.now());}
  retry(binding){
    const item=this.items.find(i=>i.binding===binding&&i.status==='failed');
    if(!item)throw new Error('没有可重试的失败审核查询。');
    item.status='scheduled';item.nextCheckAt=this.now();this.save();
  }
  async observe(item,fn) {
    item.status='running';this.save();const started=this.now();
    try {
      const result=await fn(item);
      Object.assign(item,{status:result.waiting?'scheduled':'ready',nextCheckAt:result.waiting?this.now()+60000:null,result,error:null});
    } catch(error) {Object.assign(item,{status:'failed',nextCheckAt:null,error:String(error.message).split('\n')[0].slice(0,300)});}
    const record={...item,startedAt:new Date(started).toISOString(),finishedAt:new Date(this.now()).toISOString(),elapsedSeconds:(this.now()-started)/1000};
    fs.appendFileSync(path.join(this.directory,'review-events.jsonl'),JSON.stringify(record)+'\n');this.save();
  }
}
module.exports={ReviewQueue};
