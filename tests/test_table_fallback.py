"""仅测试展示层；模拟 pyarrow DLL 故障，不调用模型。"""

import ast
import builtins
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import app


def mock_arrow_import(monkeypatch, error=None):
    original_import = builtins.__import__

    def controlled_import(name, *args, **kwargs):
        if name == "pyarrow":
            if error is not None:
                raise error
            return object()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", controlled_import)


def test_normal_environment_prefers_dataframe(monkeypatch):
    mock_arrow_import(monkeypatch)
    dataframe, markdown = Mock(), Mock()
    monkeypatch.setattr(app.st, "dataframe", dataframe)
    monkeypatch.setattr(app.st, "markdown", markdown)
    data = pd.DataFrame({"评论": ["正常评论"]})
    app.show_dataframe(data, width="stretch", hide_index=True)
    dataframe.assert_called_once_with(data, width="stretch", hide_index=True)
    markdown.assert_not_called()


@pytest.mark.parametrize("stage", ["import", "dataframe"])
def test_dll_import_error_falls_back_to_escaped_html(monkeypatch, stage):
    dll_error = ImportError("DLL load failed: Windows application control policy")
    mock_arrow_import(monkeypatch, error=dll_error if stage == "import" else None)
    dataframe = Mock(side_effect=dll_error if stage == "dataframe" else None)
    markdown = Mock()
    monkeypatch.setattr(app.st, "dataframe", dataframe)
    monkeypatch.setattr(app.st, "markdown", markdown)
    data = pd.DataFrame({"评论": ["<script>alert('x')</script> & 用户反馈"]})
    original = data.copy(deep=True)

    app.show_dataframe(data, width="stretch", hide_index=True)

    markdown.assert_called_once_with(data.to_html(index=False, escape=True), unsafe_allow_html=True)
    html = markdown.call_args.args[0]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html
    assert dataframe.call_count == (1 if stage == "dataframe" else 0)
    pd.testing.assert_frame_equal(data, original)


def test_fallback_preserves_product_index_without_changing_input(monkeypatch):
    mock_arrow_import(monkeypatch, error=ModuleNotFoundError("No module named pyarrow"))
    markdown = Mock()
    monkeypatch.setattr(app.st, "markdown", markdown)
    data = pd.DataFrame({"产品": ["飞书", "企业微信"], "总计": [2, 3]}).set_index("产品")
    original = data.copy(deep=True)
    app.show_dataframe(data, width="stretch")
    markdown.assert_called_once_with(data.reset_index().to_html(index=False, escape=True), unsafe_allow_html=True)
    pd.testing.assert_frame_equal(data, original)


def test_other_rendering_errors_are_not_silenced(monkeypatch):
    mock_arrow_import(monkeypatch)
    monkeypatch.setattr(app.st, "dataframe", Mock(side_effect=ValueError("unrelated error")))
    markdown = Mock()
    monkeypatch.setattr(app.st, "markdown", markdown)
    with pytest.raises(ValueError, match="unrelated error"):
        app.show_dataframe(pd.DataFrame({"评论": ["测试"]}))
    markdown.assert_not_called()


def test_all_table_displays_use_wrapper():
    tree = ast.parse(Path(app.__file__).read_text(encoding="utf-8"))
    direct_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "st" and node.func.attr == "dataframe"]
    wrapper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "show_dataframe")
    assert len(direct_calls) == 1
    assert direct_calls[0] in list(ast.walk(wrapper))
    # 页面数量增加后不固定调用次数；所有新增页也必须复用同一个 DLL fallback。
    pages = list((Path(app.__file__).parent / "app_pages").glob("*.py"))
    assert len(pages) == 4
    for page in pages:
        page_tree = ast.parse(page.read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr in {"dataframe", "table", "data_editor"} for node in ast.walk(page_tree))
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "show_dataframe" for node in ast.walk(page_tree))


def test_fallback_renders_in_streamlit_without_model_calls(monkeypatch):
    mock_arrow_import(monkeypatch, error=ImportError("DLL load failed"))
    test_app = AppTest.from_string(
        "import pandas as pd\n"
        "from app import show_dataframe\n"
        "show_dataframe(pd.DataFrame({'评论': ['<b>测试</b>']}), hide_index=True)\n"
    ).run(timeout=10)
    assert not test_app.exception
    assert "&lt;b&gt;测试&lt;/b&gt;" in test_app.markdown[0].value
