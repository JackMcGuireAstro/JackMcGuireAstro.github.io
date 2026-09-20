#!/usr/bin/env python3
"""Mirror one explicitly identified, public K2 host-system product; retain provenance."""
import argparse, gzip, hashlib, json, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
from astropy.io import fits
URL = 'https://archive.stsci.edu/pub/k2/lightcurves/c12/246100000/99000/ktwo246199087-c12_slc.fits'
DOCUMENTATION = 'https://archive.stsci.edu/k2/trappist1/'
KEY = 'K2-EPIC246199087-C12'
def refresh(root, force=False):
    public=root/'public/data/lightcurves'; rawdir=root/'data/observations/k2'
    public.mkdir(parents=True, exist_ok=True);rawdir.mkdir(parents=True,exist_ok=True)
    indexpath=public/'index.json'; index=json.loads(indexpath.read_text())
    prior=index.get('entries',{}).get(KEY,{})
    if not force and time.time()-prior.get('checkedUnix',0)<86400:
        index['claim']='Public HATNet discovery photometry and the explicitly identified TRAPPIST-1 K2 campaign 12 host light curve. No claim of all-mission coverage. Last good files retained after failures.'
        temp=public/'index.json.tmp';temp.write_text(json.dumps(index,indent=2)+'\n');temp.replace(indexpath)
        return
    temporary=rawdir/(KEY+'.download')
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,headers={'User-Agent':'WorldsIndex public science archive'}),timeout=90) as response, temporary.open('wb') as output:
            total=0
            while block:=response.read(1024*1024):
                total+=len(block)
                if total>200_000_000:raise ValueError('Unexpected K2 product size; prior data retained')
                output.write(block)
        digest=hashlib.sha256(temporary.read_bytes()).hexdigest()
        with fits.open(temporary,memmap=True) as hdus:
            primary=hdus[0].header;header=hdus[1].header;table=hdus[1].data
            if primary.get('KEPLERID')!=246199087 or primary.get('CAMPAIGN')!=12:raise ValueError('Target identity mismatch')
            if header.get('TIMESYS')!='TDB' or header.get('TIMEUNIT')!='d':raise ValueError('Unsupported time standard')
            if hdus[1].columns['PDCSAP_FLUX'].unit!='e-/s' or hdus[1].columns['PDCSAP_FLUX_ERR'].unit!='e-/s':raise ValueError('Unsupported flux units')
            tref=float(header['BJDREFI'])+float(header['BJDREFF'])
            t=np.asarray(table['TIME'],float)+tref; f=np.asarray(table['PDCSAP_FLUX'],float);e=np.asarray(table['PDCSAP_FLUX_ERR'],float)
            finite=np.isfinite(t)&np.isfinite(f)&np.isfinite(e)&(e>0)
            good=finite&(table['SAP_QUALITY']==0)
            if not good.any():raise ValueError('No accepted photometry')
            points=[[float(a),float(b),float(c),'Kepler','K2 C12'] for a,b,c in zip(t[good],f[good],e[good])]
            points.sort(key=lambda p:p[0]);count=len(table)
            metadata={'cadenceDays':float(header['TIMEDEL']),'timeReferenceBjd':tref,'qualityFlagExcluded':int(np.sum(table['SAP_QUALITY']!=0)),'invalidOrMissingExcluded':int(np.sum(~finite))}
        now=datetime.now(timezone.utc).isoformat()
        product={'schemaVersion':'worldsindex-lightcurve.v1','target':'TRAPPIST-1','targetScope':'host-system','targetIdentifiers':{'EPIC':'246199087','campaign':12},'source':'NASA Kepler / K2, MAST, campaign 12','sourceUrl':URL,'documentationUrl':DOCUMENTATION,'retrievedAt':now,'rawSha256':digest,'timeSystem':'BJD_TDB','timeUnit':'day','valueUnit':'e-/s','valueColumn':'PDCSAP_FLUX','uncertaintyColumn':'PDCSAP_FLUX_ERR','columns':['time','flux','pipelineFluxError','filterId','stationId'],'qualitySelection':'Finite time, PDCSAP flux and positive reported error; SAP_QUALITY=0. No sigma clipping. Full raw FITS retained locally.','uncertaintyNote':'Pipeline flux errors; normalization does not model covariance, stellar activity or residual systematics.','rawRowCount':count,'rejectedRowCount':count-len(points),'pointCount':len(points),'points':points,**metadata}
        packed=gzip.compress((json.dumps(product,separators=(',',':'))+'\n').encode(),mtime=0)
        name=KEY+'.json.gz'; staged=public/(name+'.tmp');staged.write_bytes(packed);staged.replace(public/name)
        temporary.replace(rawdir/(digest+'.fits'))
        index['entries'][KEY]={'target':'TRAPPIST-1','targetScope':'host-system','path':name,'sha256':hashlib.sha256(packed).hexdigest(),'pointCount':len(points),'sourceUrl':URL,'retrievedAt':now,'checkedUnix':time.time()}
        index['failures']=[f for f in index.get('failures',[]) if f.get('sourceUrl')!=URL]
        index['claim']='Public HATNet discovery photometry and the explicitly identified TRAPPIST-1 K2 campaign 12 host light curve. No claim of all-mission coverage. Last good files retained after failures.'
        temp=public/'index.json.tmp';temp.write_text(json.dumps(index,indent=2)+'\n');temp.replace(indexpath)
        print(json.dumps({'product':KEY,'accepted':len(points),'raw':count,'rawSha256':digest}))
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        index['failures']=[f for f in index.get('failures',[]) if f.get('sourceUrl')!=URL]+[{'sourceUrl':URL,'state':'QUERY_FAILED','errorType':type(exc).__name__,'checkedAt':datetime.now(timezone.utc).isoformat()}]
        temp=public/'index.json.tmp';temp.write_text(json.dumps(index,indent=2)+'\n');temp.replace(indexpath)
        raise
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--force',action='store_true');args=parser.parse_args();refresh(args.source,args.force)
