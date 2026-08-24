# CivitAI Provider Plugin

A marketplace provider plugin for PotionUI that integrates with CivitAI's API.

## Features

- **Model Lookup by Hash**: Find model information using SHA256 file hashes
- **Model Search**: Search for models on CivitAI with filtering options
- **Download URLs**: Get download URLs for models
- **Preview Media**: Access preview images and videos for models

## Installation

This plugin is included with PotionUI by default. To enable it:

1. Go to the Providers settings in PotionUI
2. Enable the "CivitAI Provider" plugin
3. (Optional) Configure your CivitAI API key for higher rate limits

## Configuration

### API Key (Optional)

While not required, providing a CivitAI API key allows for:
- Higher rate limits
- Access to age-restricted content (if your account has access)
- Better reliability during high traffic periods

To get an API key:
1. Log in to CivitAI
2. Go to Account Settings > API Keys
3. Create a new API key
4. Paste it in the plugin settings

### Rate Limit Delay

Configure the delay between API requests to avoid hitting rate limits. Default is 1 second.

### Download Media

When enabled, the plugin will download preview images and videos when fetching model information. This helps with offline access and faster browsing.

### Max Media Files

Limits the number of preview media files downloaded per model. Default is 10.

## Usage

Once enabled, the CivitAI provider will be used automatically when:
- Fetching model information from the Models page
- The system detects models that match CivitAI hashes

## API Capabilities

| Capability | Supported |
|------------|-----------|
| Hash Lookup | Yes |
| Search | Yes |
| Download URL | Yes |
| Model Info | Yes |
| Media Download | Yes |

## Troubleshooting

### "Rate limit exceeded" errors
- Increase the Rate Limit Delay setting
- Add an API key for higher limits

### "Connection timeout" errors
- Check your internet connection
- CivitAI may be experiencing high traffic

### Models not found
- Verify the model exists on CivitAI
- The model may have been removed or made private
