"""Credential-free HTTP checks for the merchant fork, run inside Compose."""
import json
import urllib.request


def get(path):
    with urllib.request.urlopen('http://frontend:8080' + path, timeout=20) as response:
        assert response.status == 200
        return response.read(), response.headers


def main():
    page, _ = get('/')
    assert b'Regulars' in page
    raw, headers = get('/api/v1/owner/dashboard')
    data = json.loads(raw)
    assert data['source']['synthetic'] is True
    assert data['metrics']['order_count'] > 0
    assert sum(row['net_revenue_cents'] for row in data['trend']) == data['metrics']['net_revenue_cents']
    assert data['metrics']['net_revenue_cents'] == data['metrics']['gross_revenue_cents'] - data['metrics']['refund_cents']
    assert 'no-store' in headers.get('Cache-Control', '')
    get('/api/v1/owner/status')
    csv, _ = get('/api/v1/owner/export.csv')
    assert len(csv.splitlines()) > 1
    request = urllib.request.Request('http://frontend:8080/api/v1/owner/ask', data=json.dumps({'question':'Which customers should I focus on?'}).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        answer = json.load(response)
        assert answer['answer'] and answer['evidence']
    print('Regulars HTTP smoke passed: dashboard, revenue reconciliation, source labels, CSV, investigation.')


if __name__ == '__main__':
    main()
