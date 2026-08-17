"""与前端框架无关的输出清洗回归测试。"""


def test_cleaner_strips_html_and_script_content():
    from enterprise_rag.utils import cleaner

    output = cleaner.clean_llm_output("<script>alert(1)</script>正常内容", force=True)

    assert "<script>" not in output
    assert "正常内容" in output


def test_cleaned_output_cache_is_bounded():
    from enterprise_rag.utils import cleaner

    maxsize = cleaner._clean_cached.cache_info().maxsize
    assert maxsize > 0
    for index in range(maxsize + 5):
        cleaner.clean_llm_output(f"answer-{index} unique-{index}")
    assert cleaner._clean_cached.cache_info().currsize <= maxsize
    cleaner._clean_cached.cache_clear()
