import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import assert from 'node:assert/strict';

const input=process.argv[2],output=process.argv[3];
if(!input||!output)throw Error('usage: node audit_taker_absorption_v12.mjs INPUT OUTPUT');
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const read=n=>fs.readFile(`${input}/${n}`,'utf8');
const csv=s=>{
  const lines=[],row=[];let field='',quoted=false;
  for(let i=0;i<s.length;i++){
    const ch=s[i];
    if(ch==='"'){if(quoted&&s[i+1]==='"'){field+='"';i++;}else quoted=!quoted;}
    else if(ch===','&&!quoted){row.push(field);field='';}
    else if(ch==='\n'&&!quoted){row.push(field.replace(/\r$/,''));lines.push([...row]);row.length=0;field='';}
    else field+=ch;
  }
  assert(!quoted,'unterminated CSV');
  if(field||row.length){row.push(field);lines.push([...row]);}
  const [headers,...values]=lines;
  return values.filter(v=>v.length>1).map(v=>{assert.equal(v.length,headers.length);return Object.fromEntries(headers.map((h,i)=>[h,v[i]]));});
};
const close=(a,b,t=1e-9)=>assert(Math.abs(a-b)<=t,`${a} != ${b}`);
const mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
const range=a=>[Math.min(...a),Math.max(...a)];
const manifest=JSON.parse(await read('manifest.json')),tree=JSON.parse(await read('git-tree.json'));
const root='research/taker-absorption-release-v12/run-36989682215/complete-evidence';
const lookup=new Map(tree.map(f=>[f.path,f]));
assert.equal(manifest.code_commit,'793820a5dee7577663e94f9e45157468e05850aa');
for(const f of manifest.files){assert.equal(lookup.get(`${root}/${f.file}`)?.bytes,f.bytes,'archive size mismatch '+f.file);}
const cells=csv(await read('cells.csv')),selection=JSON.parse(await read('selection.json'));
assert.equal(cells.length,96);assert.equal(selection.policies.length,0);
let rows=[],verifiedSources=new Map(),prior=0,minutes=0,excluded=0,maxArithmeticError=0;
const shardProof=[];
for(let i=0;i<8;i++){
  const bytes=Buffer.from(await read(`ledger${i}.b64`),'base64'),meta=JSON.parse(await read(`meta${i}.json`)),source=JSON.parse(await read(`source${i}.json`));
  const ledgerPath=`${root}/files/taker-absorption-release-v12-dev-${i}/independent_candidates.csv.gz`;
  assert.equal(hash(bytes),meta.ledger_sha256);
  assert.equal(hash(bytes),manifest.files.find(f=>`${root}/${f.file}`===ledgerPath).sha256);
  const blob=crypto.createHash('sha1').update(Buffer.concat([Buffer.from(`blob ${bytes.length}\0`),bytes])).digest('hex');
  assert.equal(blob,lookup.get(ledgerPath).sha);
  const shard=csv(zlib.gunzipSync(bytes).toString('utf8'));
  assert.equal(shard.length,meta.ledger_rows);assert.equal(meta.complete,true);assert.equal(meta.shard,i);
  assert.equal(source.status,'VERIFIED');assert.equal(source.errors.length,0);
  prior+=source.matched_prior_hashes;minutes+=meta.minute_official_checksums_verified;excluded+=meta.exclusions;
  for(const f of source.files){assert(!verifiedSources.has(f.symbol));verifiedSources.set(f.symbol,f.sha256);assert.equal(meta.market_hashes[f.symbol],f.sha256);}
  for(const r of shard){
    const side=Number(r.side),entry=Number(r.entry),exit=Number(r.exit),stop=Number(r.sl),hold=Number(r.max_hold_bars);
    const duration=(Number(r.exit_time)-Number(r.entry_time))/60000;
    assert(duration>0&&duration<=hold*15);assert.equal(r.split,'DEV');assert.equal(r.status,'RESOLVED');
    const gross=side*(exit/entry-1);close(gross,Number(r.gross_return));
    const fill=r.reason==='SL'?exit*(1-side*.001):exit,ratio=fill/entry;
    const fee=.002*(1+ratio),fund=.0002*duration/1440,net=side*(ratio-1)-fee-fund;
    const stopRatio=stop/entry*(1-side*.001);
    const reserve=side*(1-stopRatio)+.002*(1+stopRatio)+.0002*hold*15/1440;
    const errors=[Math.abs(net-Number(r.net40_fraction)),Math.abs(net/reserve-Number(r.net40_R))];
    maxArithmeticError=Math.max(maxArithmeticError,...errors);for(const err of errors)assert(err<1e-9);
    rows.push({...r,gross_bp:gross*10000,net_bp:net*10000,risk_R:net/reserve,cost_drag_bp:(gross-net)*10000,
      fee_bp:fee*10000,slip_bp:(gross-side*(ratio-1))*10000,funding_bp:fund*10000,
      year:new Date(Number(r.entry_time)+9*3600000).getUTCFullYear(),date:Math.floor((Number(r.entry_time)+9*3600000)/86400000)});
  }
  shardProof.push({shard:i,rows:shard.length,ledger_sha256:hash(bytes),git_blob_sha:blob,source_files:source.count,matched_prior:source.matched_prior_hashes,minute_months:meta.minute_months,exclusions:meta.exclusions});
}
assert.equal(verifiedSources.size,856);assert.equal(prior,256);assert.equal(excluded,0);
const events=new Set(rows.map(r=>[r.symbol,r.variant,r.entry_time].join('/')));assert.equal(events.size,rows.length);
const byPolicy=new Map();for(const r of rows){if(!byPolicy.has(r.policy))byPolicy.set(r.policy,[]);byPolicy.get(r.policy).push(r);}
const summaries=[];
for(const c of cells){
  const r=byPolicy.get(c.policy)||[];assert.equal(r.length,Number(c.n));
  if(!r.length){summaries.push({policy:c.policy,n:0,side:Number(c.side),flow:c.flow,hold:Number(c.hold),exit_type:c.exit_type,net40_mean_bp:null,net40_R:null,pf:null,rejections:c.rejections});continue;}
  close(mean(r.map(x=>x.net_bp)),Number(c.net40_mean_bp));close(mean(r.map(x=>x.risk_R)),Number(c.net40_R_mean));
  const pnl=r.map(x=>x.net_bp),gain=pnl.filter(x=>x>0).reduce((a,b)=>a+b,0),loss=-pnl.filter(x=>x<0).reduce((a,b)=>a+b,0);
  if(loss)close(gain/loss,Number(c.net40_pf));else assert.equal(c.net40_pf,'');assert.equal(new Set(r.map(x=>x.symbol)).size,Number(c.symbols));
  const annual={};
  for(const y of [2021,2022,2023,2024]){
    const z=r.filter(x=>x.year===y);if(!z.length)continue;
    const byDay=new Map();for(const x of z){if(!byDay.has(x.date))byDay.set(x.date,[]);byDay.get(x.date).push(x.risk_R);}
    close(mean(z.map(x=>x.net_bp)),Number(c[`year_${y}_net40_mean_bp`]));close(mean(z.map(x=>x.risk_R)),Number(c[`year_${y}_net40_R`]));
    close(mean([...byDay.values()].map(mean)),Number(c[`year_${y}_day_R`]));assert.equal(byDay.size,Number(c[`year_${y}_days`]));assert.equal(z.length,Number(c[`year_${y}_n`]));
    annual[y]={n:z.length,net_bp:mean(z.map(x=>x.net_bp)),risk_R:mean(z.map(x=>x.risk_R)),equal_active_date_R:mean([...byDay.values()].map(mean)),dates:byDay.size};
  }
  summaries.push({policy:c.policy,side:Number(c.side),flow:c.flow,hold:Number(c.hold),exit_type:c.exit_type,n:r.length,symbols:Number(c.symbols),
    gross_mean_bp:mean(r.map(x=>x.gross_bp)),net40_mean_bp:Number(c.net40_mean_bp),net40_R:Number(c.net40_R_mean),pf:c.net40_pf===''?null:Number(c.net40_pf),
    cost_drag_bp:mean(r.map(x=>x.cost_drag_bp)),fee_bp:mean(r.map(x=>x.fee_bp)),slip_bp:mean(r.map(x=>x.slip_bp)),funding_bp:mean(r.map(x=>x.funding_bp)),annual,rejections:c.rejections});
}
const nonzero=summaries.filter(s=>s.n>0);
const group=(key)=>[...new Set(summaries.map(s=>s[key]))].map(value=>{const z=summaries.filter(s=>s[key]===value);return{value,cells:z.length,n:z.reduce((a,s)=>a+s.n,0),net40_mean_bp_range:range(z.filter(s=>s.n).map(s=>s.net40_mean_bp)),positive_gross_cells:z.filter(s=>s.gross_mean_bp>0).length};});
const minuteZips=manifest.files.filter(f=>f.file.includes('/minute_original_archives/')&&f.file.endsWith('.zip'));
const recovery=JSON.parse(await read('recovery.json'));assert.equal(recovery.complete,true);assert.equal(recovery.v11_outcomes_changed,false);
const report={status:'PASS',kind:'original-ledger-arithmetic-and-durable-tree-size-audit',run:36989682215,code_commit:manifest.code_commit,dev_commit:'49b869807879f786cc46c04974f419ec707d7606',
 evidence_commit:'3d64282d571f32736ac8a9bf6ed3d7b42299b5af',archive_files:manifest.files.length,archive_bytes:manifest.files.reduce((a,f)=>a+f.bytes,0),
 official_original_zip_files:minuteZips.length,official_original_zip_bytes:minuteZips.reduce((a,f)=>a+f.bytes,0),verified_source_files:verifiedSources.size,verified_prior_hashes:prior,verified_minute_checksum_records:minutes,
 parameterized_outcomes:rows.length,cells:96,selected:0,account_scenarios:0,chronology_excluded:excluded,unique_excluded_symbol_entry_events:0,unique_entry_key_events:new Set(rows.map(x=>[x.symbol,x.key,x.entry_time].join('/'))).size,unique_coin_entry_events:new Set(rows.map(x=>[x.symbol,x.entry_time].join('/'))).size,recovered_v11_input:recovery,
 gross_positive_cells:nonzero.filter(s=>s.gross_mean_bp>0).length,net40_positive_cells:nonzero.filter(s=>s.net40_mean_bp>0).length,net40_mean_bp_range:range(nonzero.map(s=>s.net40_mean_bp)),net40_R_range:range(nonzero.map(s=>s.net40_R)),net40_pf_range:range(nonzero.filter(s=>s.pf!==0&&s.pf!==null).map(s=>s.pf)),
 cost_drag_bp_range:range(nonzero.map(s=>s.cost_drag_bp)),max_arithmetic_error:maxArithmeticError,zero_cells:summaries.length-nonzero.length,n_range:range(summaries.map(s=>s.n)),
 best_price_cell:[...nonzero].sort((a,b)=>b.net40_mean_bp-a.net40_mean_bp)[0],shards:shardProof,groups:{side:group('side'),flow:group('flow'),hold:group('hold'),exit:group('exit_type')},rejection_counts:selection.rejection_counts,
 limitations:['Overlapping parameterized outcomes are not executable account trades.','DEV ends UTC 2024-01-01; KST partial Jan1 belongs to the DEV calendar, not a processed 2024 gate.','All eight original compressed ledger bytes/hash/arithmetic checked; full archive git byte sizes checked. Other archive files have not all been downloaded and SHA256-rehashed by this audit.','No V12 market rerun, account growth claim or daily target claim.'],cells_detail:summaries};
await fs.writeFile(output,JSON.stringify(report,null,2)+'\n');
const {cells_detail,...compact}=report;console.log(JSON.stringify(compact,null,2));
