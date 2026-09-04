"""Base class for asset analyzers. TODO: implement.

Each concrete analyzer (stock/crypto/commodity) should inherit from
`AssetAnalyzer`, normalizing signals across different price scales and
applying asset-specific risk parameters (e.g. crypto = higher volatility).
"""


class AssetAnalyzer:
    """Common interface for per-asset-class analysis. TODO: implement."""
