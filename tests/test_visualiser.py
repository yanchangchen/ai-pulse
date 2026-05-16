"""Tests for the visualiser module — text preprocessing."""

import pytest
from core.visualiser import preprocess_text, extract_top_words


class TestPreprocessText:
    """Test text preprocessing for word clouds."""

    def test_lowercase(self):
        assert preprocess_text("HELLO WORLD") == "hello world"

    def test_remove_urls(self):
        result = preprocess_text("Check out https://example.com for more")
        assert "https" not in result
        assert "example" not in result

    def test_remove_special_characters(self):
        result = preprocess_text("Hello! @world #test 123")
        assert "@" not in result
        assert "#" not in result
        assert "!" not in result

    def test_collapse_whitespace(self):
        result = preprocess_text("hello    world   test")
        assert result == "hello world test"

    def test_empty_string(self):
        assert preprocess_text("") == ""

    def test_preserves_letters(self):
        result = preprocess_text("machine learning models")
        assert result == "machine learning models"


class TestExtractTopWords:
    """Test top-word extraction."""

    def test_basic_extraction(self):
        text = "model model model training training benchmark"
        result = extract_top_words(text, n=3)
        assert len(result) <= 3
        # "model" should be the top word
        assert result[0][0] == "model"
        assert result[0][1] == 3

    def test_stopwords_removed(self):
        text = "the will can new model"
        result = extract_top_words(text, n=10)
        words = [w for w, _ in result]
        assert "the" not in words
        assert "will" not in words
        assert "new" not in words
        assert "model" in words

    def test_short_words_filtered(self):
        """Words with 2 or fewer characters should be filtered."""
        text = "AI is an ok model"
        result = extract_top_words(text, n=10)
        words = [w for w, _ in result]
        assert "is" not in words
        assert "an" not in words
        assert "ok" not in words

    def test_empty_text(self):
        result = extract_top_words("", n=5)
        assert result == []
