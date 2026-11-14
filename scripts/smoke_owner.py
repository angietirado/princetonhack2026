"""Credential-free HTTP checks for the merchant fork, run inside Compose."""
import json
import urllib.request
from urllib.parse import quote, urlencode


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
    raw, _ = get('/api/v1/owner/status')
    capability = json.loads(raw)
    if capability.get('can_save_pilots'):
        raw, _ = get('/api/v1/owner/pilots?limit=1')
        saved = json.loads(raw)
        assert saved['pagination']['limit'] == 1
        assert len(saved['pilots']) <= 1
        if saved['pilots']:
            raw, _ = get('/api/v1/owner/pilots/' + saved['pilots'][0]['id'])
            record = json.loads(raw)
            assert record['id'] == saved['pilots'][0]['id']
            assert record['status'] == 'saved_draft'
            assert record['source'] == saved['pilots'][0]['source']
            assert all(note['verification'] == 'owner_reported' for note in record['notes'])
    person = data['customers'][0]
    query = urlencode({**data['period'], 'expected_revision': data['source']['revision']})
    raw, _ = get('/api/v1/owner/customers/' + quote(person['id'], safe='') + '?' + query)
    detail = json.loads(raw)
    assert detail['period_metrics']['net_revenue_cents'] == person['revenue_cents']
    assert detail['observed_history']['orders'] >= detail['period_metrics']['orders']
    assert detail['source']['dataset_id'] == data['source']['dataset_id']
    raw, _ = get('/api/v1/owner/compare?' + urlencode(data['period']))
    comparison = json.loads(raw)
    assert sum(row['delta_cents'] for row in comparison['groups']) == comparison['delta_cents']
    csv, _ = get('/api/v1/owner/export.csv')
    assert len(csv.splitlines()) > 1
    request = urllib.request.Request('http://frontend:8080/api/v1/owner/ask', data=json.dumps({'question':'Which customers should I focus on?'}).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        answer = json.load(response)
        assert answer['answer'] and answer['evidence']
    print('Regulars HTTP smoke passed: dashboard, customer evidence, revenue reconciliation, comparison, source labels, saved drafts, CSV, investigation.')


if __name__ == '__main__':
    main()
