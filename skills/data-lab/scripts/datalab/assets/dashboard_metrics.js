/* Pure metric semantics for offline dashboards; checked against Python fixtures. */
(function(root) {
  'use strict';
  const missing = value => value == null || (typeof value === 'number' && Number.isNaN(value));
  const equal = (a,b) => a === b || (typeof a === 'boolean' && typeof b === 'number' && Number(a) === b) || (typeof b === 'boolean' && typeof a === 'number' && a === Number(b));
  const ops = new Set(['eq','ne','in','not_in','is_null','not_null','gt','ge','lt','le']);
  function filterRows(rows, filters=[]) {
    if(!Array.isArray(filters)) throw new Error('Metric filters must be a list');
    filters.forEach(f=>{if(!f || !ops.has(f.op) || typeof f.column!=='string')throw new Error('Invalid metric filter');});
    return rows.filter(row=>filters.every(f=>{
      if(!Object.prototype.hasOwnProperty.call(row,f.column))throw new Error('Unknown filter column: '+f.column);
      const value=row[f.column];
      if(f.op==='is_null')return missing(value);
      if(f.op==='not_null')return !missing(value);
      if(missing(value))return false;
      if(f.op==='eq')return equal(value,f.value);
      if(f.op==='ne')return !equal(value,f.value);
      if(f.op==='in'||f.op==='not_in'){
        if(!Array.isArray(f.value))throw new Error('Membership filter requires a list');
        const contains=f.value.some(v=>equal(value,v));return f.op==='in'?contains:!contains;
      }
      if(typeof value!==typeof f.value && !(typeof value==='number'&&typeof f.value==='boolean') && !(typeof value==='boolean'&&typeof f.value==='number'))throw new Error('Filter values have incompatible types');
      return f.op==='gt'?value>f.value:f.op==='ge'?value>=f.value:f.op==='lt'?value<f.value:value<=f.value;
    }));
  }
  function numeric(value) {
    if(missing(value)||value==='')return null;
    if(typeof value==='string' && !value.trim())throw new Error('Numeric metric contains whitespace text');
    if(typeof value==='string'){
      const token=value.trim();
      if(/^[+-]?inf(?:inity)?$/i.test(token))return null;
      if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(token))throw new Error('Numeric metric contains invalid numeric text');
    }
    const parsed=Number(value);
    if(Number.isNaN(parsed))throw new Error('Numeric metric contains nonnumeric text');
    return Number.isFinite(parsed)?parsed:null;
  }
  function sum(values) {let total=0,correction=0;for(const value of values){const t=total+value;correction+=Math.abs(total)>=Math.abs(value)?(total-t)+value:(value-t)+total;total=t;}return total+correction;}
  function computeMetric(rows,spec) {
    if(!spec || typeof spec.aggregation!=='string')throw new Error('Metric aggregation is required');
    const eligible=filterRows(rows,spec.filters||[]),agg=spec.aggregation;
    const result={value:null,input_rows:rows.length,eligible_rows:eligible.length,valid_rows:0,excluded_rows:0,undefined_reason:null};
    if(agg==='count_rows'){result.value=result.valid_rows=eligible.length;return result;}
    if(!spec.column)throw new Error('Metric column is required');
    for(const row of eligible)if(!Object.prototype.hasOwnProperty.call(row,spec.column))throw new Error('Unknown metric column');
    if(agg==='count_nonmissing'||agg==='count_distinct'){
      const values=eligible.map(r=>r[spec.column]).filter(v=>!missing(v));result.valid_rows=values.length;result.excluded_rows=eligible.length-values.length;
      result.value=agg==='count_nonmissing'?values.length:new Set(values.map(v=>JSON.stringify(typeof v==='boolean'?Number(v):v))).size;return result;
    }
    const pairs=eligible.map(row=>[numeric(row[spec.column]),row]);
    if(agg==='ratio'||agg==='weighted_mean'){
      const other=agg==='ratio'?spec.denominator:spec.weight;
      if(!other)throw new Error('Ratio/weighted metric requires its related column');
      const all=pairs.map(([x,row])=>{if(!Object.prototype.hasOwnProperty.call(row,other))throw new Error('Unknown denominator/weight column');return [x,numeric(row[other])];});
      if(all.some(([,y])=>y!==null&&y<0))throw new Error('Denominator/weight must not be negative');
      const valid=all.filter(([x,y])=>x!==null&&y!==null);
      result.valid_rows=valid.length;result.excluded_rows=eligible.length-valid.length;
      if(!valid.length){result.undefined_reason='no_valid_rows';return result;}
      const denominator=sum(valid.map(([,y])=>y));result[agg==='ratio'?'denominator_sum':'weight_sum']=denominator;
      if(denominator===0){result.undefined_reason=agg==='ratio'?'zero_denominator':'zero_weight_sum';return result;}
      const numerator=sum(valid.map(([x,y])=>agg==='ratio'?x:x*y));result[agg==='ratio'?'numerator_sum':'weighted_sum']=numerator;
      result.value=numerator/denominator;if(!Number.isFinite(result.value))throw new Error('Metric aggregation is not finite');if(Number.isInteger(result.value)&&!Number.isSafeInteger(result.value))throw new Error('离线聚合超过安全整数范围；请明确换算单位或使用实时Python计算。');return result;
    }
    const values=pairs.map(([x])=>x).filter(v=>v!==null);result.valid_rows=values.length;result.excluded_rows=eligible.length-values.length;
    if(!values.length){result.undefined_reason='no_valid_rows';return result;}
    if(agg==='sum')result.value=sum(values);
    else if(agg==='mean')result.value=sum(values)/values.length;
    else if(agg==='median'){values.sort((a,b)=>a-b);const middle=Math.floor(values.length/2);result.value=values.length%2?values[middle]:(values[middle-1]+values[middle])/2;}
    else throw new Error('Unsupported metric aggregation: '+agg);
    if(!Number.isFinite(result.value))throw new Error('Metric aggregation is not finite');if(Number.isInteger(result.value)&&!Number.isSafeInteger(result.value))throw new Error('离线聚合超过安全整数范围；请明确换算单位或使用实时Python计算。');return result;
  }
  function fixedEven(value,precision) {
    // Round the exact IEEE-754 value with ties-to-even, matching Python's f format.
    const negative=value<0||Object.is(value,-0),buffer=new ArrayBuffer(8),view=new DataView(buffer);
    view.setFloat64(0,Math.abs(value),false);
    const high=view.getUint32(0,false),low=view.getUint32(4,false),exponent=(high>>>20)&2047;
    let numerator=(BigInt(high&0xfffff)<<32n)|BigInt(low),power;
    if(exponent){numerator|=1n<<52n;power=exponent-1023-52;}else power=-1074;
    let denominator=1n;
    if(power>=0)numerator<<=BigInt(power);else denominator<<=BigInt(-power);
    numerator*=10n**BigInt(precision);
    let rounded=numerator/denominator;const remainder=numerator%denominator;
    if(remainder*2n>denominator||(remainder*2n===denominator&&rounded%2n))rounded++;
    const digits=rounded.toString().padStart(precision+1,'0');
    const integer=(precision?digits.slice(0,-precision):digits).replace(/\B(?=(\d{3})+(?!\d))/g,',');
    return (negative?'-':'')+integer+(precision?'.'+digits.slice(-precision):'');
  }
  function formatValue(value,spec={},preferences={}) {
    if(value===null||value===undefined||!Number.isFinite(value))return preferences.display?.missing_label||'—';
    const precision=spec.precision??preferences.display?.precision??2;
    const shown=spec.unit==='percent'?value*100:value;
    if(!Number.isFinite(shown))throw new Error('Formatted value exceeded finite range');
    return (spec.unit==='currency'?spec.currency+' ':'')+fixedEven(shown,precision)+(spec.unit==='percent'?'%':'');
  }
  const api={computeMetric,filterRows,formatValue};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.DataLabMetrics=api;
})(typeof globalThis!=='undefined'?globalThis:this);
