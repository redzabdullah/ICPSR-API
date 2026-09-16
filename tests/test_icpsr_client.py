import unittest
from unittest.mock import Mock
from urllib.parse import quote_plus

from src.icpsr_client import ICPSRAPIError, ICPSRClient, ICPSRConfig


class ICPSRClientRedactionTests(unittest.TestCase):
    def setUp(self):
        self.api_key = "client-key-sensitive"
        self.api_secret = "secret value/sensitive"
        self.client = ICPSRClient(ICPSRConfig(
            api_key=self.api_key,
            api_secret=self.api_secret,
        ))

    def assert_credentials_redacted(self, message: str):
        self.assertNotIn(self.api_key, message)
        self.assertNotIn(self.api_secret, message)
        self.assertNotIn(quote_plus(self.api_key), message)
        self.assertNotIn(quote_plus(self.api_secret), message)
        self.assertIn("[REDACTED]", message)

    def test_non_json_token_echo_redacts_key_and_secret(self):
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response.text = (
            f"client_id={quote_plus(self.api_key)}&"
            f"client_secret={quote_plus(self.api_secret)}"
        )
        self.client.session.post = Mock(return_value=response)

        with self.assertRaises(ICPSRAPIError) as error:
            self.client._get_oauth_token()

        self.assert_credentials_redacted(str(error.exception))

    def test_general_api_error_redacts_key_and_secret(self):
        response = Mock()
        response.status_code = 400
        response.json.return_value = {
            "client_id": self.api_key,
            "client_secret": self.api_secret,
        }

        with self.assertRaises(ICPSRAPIError) as error:
            self.client._raise_for_error(response)

        self.assert_credentials_redacted(str(error.exception))


if __name__ == "__main__":
    unittest.main()
