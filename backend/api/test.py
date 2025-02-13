import requests

def test_visitor_counter():
    url = "http://localhost:7071/api/counter"
    headers = {
        "x-correlation-id": "test-123"
    }
    
    response = requests.post(url, headers=headers)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
    
    return response

# Run the test
test_visitor_counter()