# Security Policy

## Supported Versions
Only the latest release on the `main` branch receives active security updates.

## Reporting a Vulnerability
If you discover a security vulnerability or potential credential exposure within JobFarm:
1. **Do NOT open a public GitHub issue.**
2. Please report security issues privately by creating a GitHub Security Advisory or reaching out via project maintainers.
3. Include details of the vulnerability, reproduction steps, and potential impact.

## Best Practices
- Never commit `.env` files or API credentials to version control.
- Use secret managers such as Infisical or AWS Secrets Manager when deploying to remote servers or cloud VMs.
- Always use encrypted proxy tunnels when routing browser sessions.
