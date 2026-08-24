"""
External API mocks for testing without network calls.

These mocks replace HTTP requests to external services like Civitai and
HuggingFace with fake responses, allowing tests to run offline.
"""

import pytest
from unittest.mock import patch, MagicMock, Mock
from typing import Optional, Dict, Any


@pytest.fixture
def mock_civitai():
    """
    Mock Civitai API calls to avoid network requests.

    This fixture patches CivitaiService methods to return fake model
    information and download URLs without actually calling the Civitai API.

    The mock provides realistic responses for:
    - Model metadata lookup
    - Version information
    - Download URLs
    - Thumbnail URLs

    Usage:
        def test_civitai_integration(mock_civitai):
            # Civitai API calls are mocked
            model_info = civitai_service.fetch_model(12345)
            # model_info is fake data, no network call made
    """
    fake_model_data = {
        'id': 12345,
        'name': 'Test Model',
        'description': 'A test model for unit tests',
        'type': 'Checkpoint',
        'modelVersions': [
            {
                'id': 67890,
                'name': 'v1.0',
                'baseModel': 'SD 1.5',
                'files': [
                    {
                        'name': 'test_model.safetensors',
                        'sizeKB': 2000000,
                        'type': 'Model',
                        'metadata': {
                            'fp': 'fp16',
                            'size': 'pruned'
                        },
                        'downloadUrl': 'https://fake.civitai.com/model.safetensors'
                    }
                ],
                'images': [
                    {
                        'url': 'https://fake.civitai.com/preview.jpg',
                        'width': 1024,
                        'height': 1024
                    }
                ]
            }
        ],
        'creator': {
            'username': 'TestCreator'
        },
        'tags': ['test', 'fake', 'mock']
    }

    # Nothing to patch: model metadata is fetched through the provider registry
    # (plugin-provided), not a core service. The fixture supplies the fake payload
    # a provider would return; tests that need a provider mock the registry.
    yield fake_model_data


@pytest.fixture
def mock_huggingface():
    """
    Mock HuggingFace Hub API calls to avoid network requests.

    This fixture patches HuggingFace Hub functions to return fake
    file paths instead of actually downloading models from HuggingFace.

    The mock provides:
    - Fake model repository access
    - Fake file downloads
    - Fake model info lookups

    Usage:
        def test_huggingface_integration(mock_huggingface):
            # HuggingFace API calls are mocked
            model_path = hf_hub_download(repo_id="test/model", filename="model.safetensors")
            # model_path is fake, no download occurred
    """
    fake_model_path = '/fake/huggingface/models/test_model.safetensors'
    fake_model_info = {
        'id': 'test/model',
        'modelId': 'test/model',
        'sha': 'abc123def456',
        'pipeline_tag': 'text-to-image',
        'tags': ['diffusers', 'stable-diffusion'],
        'siblings': [
            {'rfilename': 'model.safetensors'},
            {'rfilename': 'config.json'}
        ]
    }

    with patch('huggingface_hub.hf_hub_download', return_value=fake_model_path), \
         patch('huggingface_hub.model_info', return_value=fake_model_info), \
         patch('huggingface_hub.list_repo_files',
               return_value=['model.safetensors', 'config.json']), \
         patch('huggingface_hub.snapshot_download', return_value='/fake/huggingface/models/test_model'):
        yield fake_model_path


@pytest.fixture
def mock_requests_get():
    """
    Mock HTTP GET requests using the requests library.

    This fixture patches requests.get to return fake responses
    without making actual network calls. Useful for testing code
    that downloads files or fetches data from arbitrary URLs.

    The mock returns different responses based on the URL:
    - Image URLs return fake image data
    - JSON endpoints return fake JSON
    - File downloads return fake binary data

    Usage:
        def test_http_download(mock_requests_get):
            # HTTP requests are mocked
            response = requests.get("https://example.com/file.txt")
            # response is fake, no network call made
    """
    def fake_get(url, **kwargs):
        """
        Fake requests.get that returns appropriate mock responses.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.ok = True

        # Determine response content based on URL
        if url.endswith(('.jpg', '.png', '.webp', '.jpeg')):
            # Image URL - return fake image bytes
            from PIL import Image
            import io
            img = Image.new('RGB', (512, 512), color='blue')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            mock_response.content = img_bytes.getvalue()
            mock_response.headers = {'content-type': 'image/png'}

        elif url.endswith('.json') or 'api' in url:
            # JSON API - return fake JSON
            mock_response.json = Mock(return_value={'status': 'success', 'data': {}})
            mock_response.content = b'{"status": "success", "data": {}}'
            mock_response.headers = {'content-type': 'application/json'}

        else:
            # Generic file - return fake binary data
            mock_response.content = b'fake file content for testing'
            mock_response.headers = {'content-type': 'application/octet-stream'}

        # Mock streaming
        mock_response.iter_content = Mock(return_value=[mock_response.content])

        # Mock headers for download size
        mock_response.headers['content-length'] = str(len(mock_response.content))

        return mock_response

    with patch('requests.get', side_effect=fake_get):
        yield


@pytest.fixture
def mock_requests_post():
    """
    Mock HTTP POST requests using the requests library.

    This fixture patches requests.post to return fake responses
    suitable for testing API interactions that require POST requests.

    Usage:
        def test_api_post(mock_requests_post):
            # POST requests are mocked
            response = requests.post("https://api.example.com/generate")
    """
    def fake_post(url, **kwargs):
        """
        Fake requests.post that returns success responses.
        """
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json = Mock(return_value={
            'status': 'success',
            'id': 'fake-id-123',
            'message': 'Request processed successfully'
        })
        mock_response.content = b'{"status": "success"}'
        mock_response.headers = {'content-type': 'application/json'}

        return mock_response

    with patch('requests.post', side_effect=fake_post):
        yield


@pytest.fixture
def mock_aiohttp_session():
    """
    Mock aiohttp ClientSession for async HTTP requests.

    This fixture patches aiohttp to return fake responses for
    async HTTP operations without making real network calls.

    Usage:
        async def test_async_download(mock_aiohttp_session):
            # Async HTTP requests are mocked
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    data = await response.read()
    """
    class FakeResponse:
        def __init__(self, status=200, content=b'fake content'):
            self.status = status
            self.content_data = content
            self.headers = {'content-type': 'application/octet-stream'}

        async def read(self):
            return self.content_data

        async def json(self):
            return {'status': 'success', 'data': {}}

        async def text(self):
            return self.content_data.decode('utf-8')

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class FakeSession:
        async def get(self, url, **kwargs):
            return FakeResponse()

        async def post(self, url, **kwargs):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def close(self):
            pass

    with patch('aiohttp.ClientSession', return_value=FakeSession()):
        yield


@pytest.fixture
def mock_all_external_apis(mock_civitai, mock_huggingface, mock_requests_get,
                           mock_requests_post, mock_aiohttp_session):
    """
    Convenience fixture that mocks all external API calls.

    This fixture applies all external API mocks at once, ensuring
    that tests never make actual network requests to any external service.

    Usage:
        def test_with_no_network(mock_all_external_apis):
            # All external API calls are mocked
            # Test can run completely offline
    """
    yield
