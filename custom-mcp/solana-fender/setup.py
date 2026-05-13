"""Setup configuration for solana-fender MCP wrapper.

Original Plamen work. Licensed under MIT — see LICENSE in this directory
(same terms as the root Plamen LICENSE).
"""
from setuptools import setup, find_packages

setup(
    name="solana-fender-mcp",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["mcp"],
    entry_points={
        "console_scripts": [
            "solana-fender-mcp=solana_fender_mcp.__main__:run",
        ],
    },
)
