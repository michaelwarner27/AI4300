"""
Setup configuration for HaRVey vector ingestion and semantic query pipeline.
"""

from setuptools import setup, find_packages

setup(
    name="harvey",
    version="0.1.0",
    description="Vector ingestion and semantic search pipeline using LangGraph",
    author="Michael",
    python_requires=">=3.9",
    packages=find_packages(),
    install_requires=[
        "pypdf>=4.0.0",
        "langchain>=0.1.0",
        "langchain_text_splitters>=0.0.1",
        "langgraph>=0.0.1",
        "chromadb>=0.4.0",
        "requests>=2.31.0",
        "python-dotenv>=1.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "harvey-ingest=ingestion_main:main",
            "harvey-query=query_main:main",
        ],
    },
)
