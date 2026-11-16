/* Preview warnings only; no eligibility, authorization or recurrence inference. */
(function(global){
    function minutes(value,end=false){
        if(end && value==='24:00')return 1440;
        if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value || ''))return null;
        return Number(value.slice(0,2))*60+Number(value.slice(3));
    }
    function day(value){
        if(!/^\d{4}-\d{2}-\d{2}$/.test(value || '') || value.startsWith('0000'))return null;
        const date=new Date(value+'T00:00:00Z');
        return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10)===value ? date.getTime()/86400000 : null;
    }
    function inspect(events){
        const warnings=new Map(events.map(e=>[e.id,[]])),valid=[];let duplicates=0,conflicts=0;
        for(const e of events){
            const a=minutes(e.start),end=minutes(e.end,true),c=e.canonical?.revision.content;
            const b=end===null?null:end+(c?.end_day_offset===1?1440:0),date=day(e.date);
            if(a===null || b===null || b<=a || b-a>1440)warnings.get(e.id).push('Check start and end times');
            if(date===null)warnings.get(e.id).push('Date not confirmed');
            if(!c?.recurrence)warnings.get(e.id).push('Repeat schedule not confirmed');
            if(a!==null && b!==null && b>a && b-a<=1440 && date!==null)valid.push({e,a:date*1440+a,b:date*1440+b});
        }
        for(let i=0;i<valid.length;i++)for(let j=i+1;j<valid.length;j++){
            const x=valid[i],y=valid[j];
            if(x.a===y.a && x.b===y.b && x.e.subject===y.e.subject && x.e.type===y.e.type && x.e.venue===y.e.venue){
                duplicates++;for(const item of [x,y])warnings.get(item.e.id).push('Possible duplicate class');
            }else if(x.a<y.b && y.a<x.b){conflicts++;for(const item of [x,y])warnings.get(item.e.id).push('Overlaps another class');}
        }
        return {warnings,duplicates,conflicts};
    }
    global.STAIRCalendarChecks={inspect};
})(globalThis);
