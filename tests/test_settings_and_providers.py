#!/usr/bin/env python3
"""
Unit tests for Universal LLM Provider Integration and Settings Dialog.
Covers:
- Provider Presets and Configuration Resolution (get_active_provider_info)
- Environment variable overrides in load_config (OpenAI, Ollama, OpenRouter, DeepSeek)
- Secure config persistence (0600 file permissions)
- Non-blocking connection tester error handling (test_llm_connection)
"""

import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from lexi import (
    DEFAULT_CONFIG,
    PROVIDER_PRESETS,
    get_active_provider_info,
    test_llm_connection,
    load_config,
    save_config
)


class TestProviderIntegration(unittest.TestCase):

    def test_default_config_has_all_providers(self):
        """Verifies DEFAULT_CONFIG defines provider presets for zero-friction onboarding."""
        self.assertIn('provider', DEFAULT_CONFIG)
        self.assertIn('openai_api_base', DEFAULT_CONFIG)
        self.assertIn('openai_model', DEFAULT_CONFIG)
        self.assertIn('ollama_api_base', DEFAULT_CONFIG)
        self.assertIn('ollama_model', DEFAULT_CONFIG)
        self.assertIn('omniroute_api_base', DEFAULT_CONFIG)
        self.assertIn('custom_api_base', DEFAULT_CONFIG)

    def test_provider_presets_metadata(self):
        """Verifies presets have name, default_model, api_base, requires_key, and helpful hints."""
        for key in ['openrouter', 'openai', 'deepseek', 'groq', 'ollama', 'omniroute', 'custom']:
            self.assertIn(key, PROVIDER_PRESETS)
            preset = PROVIDER_PRESETS[key]
            self.assertIn('name', preset)
            self.assertIn('api_base', preset)
            self.assertIn('default_model', preset)
            self.assertIn('requires_key', preset)
            self.assertIn('hint', preset)

        self.assertFalse(PROVIDER_PRESETS['ollama']['requires_key'])
        self.assertFalse(PROVIDER_PRESETS['custom']['requires_key'])
        self.assertTrue(PROVIDER_PRESETS['openai']['requires_key'])
        self.assertTrue(PROVIDER_PRESETS['openrouter']['requires_key'])
        self.assertTrue(PROVIDER_PRESETS['groq']['requires_key'])

    def test_get_active_provider_info_resolution(self):
        """Tests provider resolution for each supported backend."""
        cfg_or = {'provider': 'openrouter', 'api_key': 'test_or_key', 'model': 'test_or_model'}
        info_or = get_active_provider_info(cfg_or)
        self.assertEqual(info_or['provider'], 'openrouter')
        self.assertEqual(info_or['api_key'], 'test_or_key')
        self.assertEqual(info_or['model'], 'test_or_model')
        self.assertTrue(info_or['requires_key'])

        cfg_oa = {
            'provider': 'openai',
            'openai_api_key': 'test_oa_key',
            'openai_api_base': 'https://api.openai.com/v1',
            'openai_model': 'gpt-4o'
        }
        info_oa = get_active_provider_info(cfg_oa)
        self.assertEqual(info_oa['provider'], 'openai')
        self.assertEqual(info_oa['api_key'], 'test_oa_key')
        self.assertEqual(info_oa['model'], 'gpt-4o')

        cfg_ol = {
            'provider': 'ollama',
            'ollama_api_base': 'http://localhost:11434/v1',
            'ollama_model': 'llama3.2'
        }
        info_ol = get_active_provider_info(cfg_ol)
        self.assertEqual(info_ol['provider'], 'ollama')
        self.assertEqual(info_ol['api_key'], '')
        self.assertEqual(info_ol['model'], 'llama3.2')
        self.assertFalse(info_ol['requires_key'])

    def test_env_overrides_in_load_config(self):
        """Verifies environment variables automatically configure providers."""
        env_patch = {
            'OPENAI_API_KEY': 'synthetic-oa-key',
            'OPENAI_BASE_URL': 'https://custom.openai.local/v1',
            'OPENAI_MODEL': 'gpt-4o-mini-env',
            'OLLAMA_HOST': 'localhost:11435'
        }
        with patch.dict(os.environ, env_patch, clear=False):
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg_path = os.path.join(tmpdir, 'config.json')
                with patch('lexi.CONFIG_FILE', cfg_path):
                    cfg = load_config()
                    self.assertEqual(cfg['openai_api_key'], 'synthetic-oa-key')
                    self.assertEqual(cfg['openai_api_base'], 'https://custom.openai.local/v1')
                    self.assertEqual(cfg['openai_model'], 'gpt-4o-mini-env')
                    self.assertEqual(cfg['ollama_api_base'], 'http://localhost:11435/v1')

    def test_save_config_file_permissions(self):
        """Verifies save_config writes file with strict 0600 mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, 'config.json')
            with patch('lexi.CONFIG_FILE', cfg_path):
                sample_cfg = dict(DEFAULT_CONFIG)
                sample_cfg['provider'] = 'ollama'
                self.assertTrue(save_config(sample_cfg))
                self.assertTrue(os.path.exists(cfg_path))
                mode = stat.S_IMODE(os.stat(cfg_path).st_mode)
                self.assertEqual(mode, 0o600, 'config.json must have 0600 permissions')

    def test_connection_tester_empty_base(self):
        """Verifies test_llm_connection rejects empty base URLs safely."""
        ok, msg, lat = test_llm_connection('custom', '', '', 'model')
        self.assertFalse(ok)
        self.assertIn('Base URL is required', msg)

    def test_connection_tester_offline_handling(self):
        """Verifies test_llm_connection catches connection refused gracefully without crashing."""
        ok, msg, lat = test_llm_connection('ollama', 'http://127.0.0.1:59999/v1', '', 'llama3.2', timeout=1)
        self.assertFalse(ok)
        self.assertTrue('Connection Refused' in msg or 'Network Error' in msg or 'Error' in msg)

    def test_settings_dialog_instantiation_and_provider_switching(self):
        """Verifies LexiSettingsDialog instantiates without attribute errors on GTK PasswordEntry."""
        import lexi
        d = lexi.LexiSettingsDialog(config=dict(DEFAULT_CONFIG))
        self.assertIsNotNone(d)
        for idx in range(len(d.provider_keys)):
            d.prov_dd.set_selected(idx)
            self.assertEqual(d.current_provider, d.provider_keys[idx])

    def test_open_site_schema_with_https(self):
        """Verifies open_site schema accepts https:// and query strings without SchemaError."""
        from lexi_capabilities import REGISTRY
        cap = REGISTRY["open_site"]
        urls = [
            "https://github.com/search?q=artemus",
            "https://github.com/artemus",
            "https://google.com/search?q=artemus+github",
            "http://example.com/test",
            "github.com/artemus"
        ]
        for u in urls:
            res = cap.validate({"site": u})
            self.assertIn("site", res)

    def test_chrome_search_intent_routing(self):
        """Verifies 'open chrome search <query>' routes directly to open_site."""
        import lexi_intents as I
        r = I.route("open chrome search artemus github")
        self.assertEqual(r.kind, I.ACTION)
        self.assertEqual(r.name, "open_site")
        self.assertIn("artemus+github", r.args["site"])

    def test_deepseek_provider_resolution_and_model_normalization(self):
        """Verifies deepseek provider resolves deepseek-flash and normalizes model variants."""
        from lexi import get_active_provider_info, normalize_deepseek_model, PROVIDER_PRESETS
        self.assertEqual(PROVIDER_PRESETS['deepseek']['default_model'], 'deepseek-flash')
        
        cfg = {
            'provider': 'deepseek',
            'deepseek_api_key': 'test_ds_key',
            'deepseek_api_base': 'https://api.deepseek.com',
            'deepseek_model': 'deepseek/deepseek-v4.1-flash'
        }
        info = get_active_provider_info(cfg)
        self.assertEqual(info['provider'], 'deepseek')
        self.assertEqual(info['api_key'], 'test_ds_key')
        self.assertEqual(info['model'], 'deepseek-flash')

        # Test normalization aliases
        self.assertEqual(normalize_deepseek_model('deepseek-v4.1-flash'), 'deepseek-flash')
        self.assertEqual(normalize_deepseek_model('deepseek/deepseek-v4.1-flash'), 'deepseek-flash')
        self.assertEqual(normalize_deepseek_model('deepseek-flash-vision'), 'deepseek-flash')
        self.assertEqual(normalize_deepseek_model('deepseek-v4-flash'), 'deepseek-flash')
        self.assertEqual(normalize_deepseek_model('deepseek-v4-pro'), 'deepseek-v4-pro')
        self.assertEqual(normalize_deepseek_model('deepseek-chat'), 'deepseek-chat')


if __name__ == '__main__':
    unittest.main()


