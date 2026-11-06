"""Synthetic-only backend HTTP benchmark: 20 clients, 10,000 leads, real AI.

Run inside an empty development/staging instance sharing the app network.
This measures Gunicorn/API/DB, excluding reverse-proxy TLS overhead.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.cookies import SimpleCookie
import json
import os
import secrets
import statistics
import time
import urllib.request
import urllib.error
import bcrypt
from api.db import get_conn
from api.postgres import system_context
from api.config import Config


def request(path, token=None, body=None):
    config=Config.load()
    headers={'Content-Type':'application/json','Origin':config.base_url}
    if token:headers['Cookie']=token
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request('http://127.0.0.1:8000'+path,data=data,headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    start=time.perf_counter()
    try:
        response=opener.open(req,timeout=180)
    except urllib.error.HTTPError as error:response=error
    payload=response.read()
    return response.status,time.perf_counter()-start,response.headers,payload


@system_context("operator: load-test fixture")
def main():
    config=Config.load()
    if config.environment not in ('development','staging'):
        raise ValueError('Load fixture only allowed in empty development/staging')
    password=secrets.token_urlsafe(24)
    hashed=bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode()
    with get_conn() as conn:
        conn.begin()
        conn.execute('SELECT pg_advisory_xact_lock(734102621)')
        if conn.execute('SELECT count(*) FROM users').fetchone()[0]:
            raise ValueError('Benchmark requires a fresh instance with no users')
        accounts=[]
        for i in range(21):
            role='admin' if i==0 else 'partner'
            email=f'load-{i}@example.test'
            uid=conn.execute('INSERT INTO users(email,password,name,role,company) VALUES (?,?,?,?,?)',
                (email,hashed,f'Synthetic {i}',role,'Synthetic')).lastrowid
            accounts.append((email,role,uid))
        with conn.raw.cursor() as cursor:
            cursor.executemany("INSERT INTO leads(id,client_name,client_email,company,value,partner_id) VALUES (%s,%s,%s,%s,%s,%s)",
                [(f'LOAD-{i:05d}',f'Synthetic client {i}',f'client-{i}@example.test','Synthetic',10000,accounts[1+i%20][2]) for i in range(10000)])
    sessions=[]
    for email,role,uid in accounts:
        status,_,headers,_=request('/api/auth/login',body={'email':email,'password':password,'role':role})
        if status!=200:raise RuntimeError('Synthetic login failed')
        cookie=SimpleCookie();cookie.load(headers['Set-Cookie'])
        sessions.append('; '.join(f'{key}={value.value}' for key,value in cookie.items()))
    # Both shapes of the same read, so one run shows the cost of each:
    # LEADS_PAGE is what the producer's list actually sends
    # (one page, ordered and counted in the database); LEADS_ALL is the
    # unbounded read the partner board and the payout forecast still use.
    LEADS_PAGE='/api/leads?page=1&per_page=25&sort=attention&dir=asc'
    LEADS_ALL='/api/leads'
    def client(i):
        observations=[]
        for _ in range(10):
            for path in (LEADS_PAGE,LEADS_ALL,'/api/stats','/api/auth/me'):
                status,elapsed,_,_=request(path,sessions[i+1])
                observations.append((path,status,elapsed*1000))
        return observations
    def ai():
        status,elapsed,_,_=request('/api/agent/chat',sessions[0],{'prompt':'Summarise the number of leads. Do not change anything.'})
        return {'status':status,'seconds':round(elapsed,3),'real_model':True}
    def run_phase(with_ai):
        start=time.perf_counter()
        with ThreadPoolExecutor(21) as pool:
            future=pool.submit(ai) if with_ai else None
            observations=[item for group in pool.map(client,range(20)) for item in group]
            ai_result=future.result() if future else None
        result={'requests':len(observations),'seconds':round(time.perf_counter()-start,3),'ai':ai_result,'routes':{}}
        for path in sorted({r[0] for r in observations}):
            rows=[r for r in observations if r[0]==path];values=sorted(r[2] for r in rows)
            result['routes'][path]={'errors':sum(r[1]!=200 for r in rows),'p50_ms':round(statistics.median(values),2),
                'p95_ms':round(values[int(len(values)*.95)-1],2),'p99_ms':round(values[int(len(values)*.99)-1],2)}
        return result
    report={'time':datetime.utcnow().isoformat(),'concurrent_users':20,'leads':10000,
            'scope':'backend HTTP; excludes proxy TLS','baseline':run_phase(False),'during_ai':run_phase(True)}
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
