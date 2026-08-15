"""Unit tests for the pure helpers in scribd-downloader.py."""

import pytest


class TestExtractDocumentId:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.scribd.com/document/123456789/Document-Title",
            "https://www.scribd.com/doc/123456789/Document-Title",
            "https://www.scribd.com/document/123456789",
            "https://www.scribd.com/document/123456789/",
            "http://www.scribd.com/doc/123456789/Title",
            "https://scribd.com/document/123456789/Title",
            "https://ru.scribd.com/document/123456789/Title",
            "www.scribd.com/document/123456789/Title",
            "https://www.scribd.com/presentation/123456789/Title",
            "https://www.scribd.com/embeds/123456789/content",
            "  https://www.scribd.com/document/123456789/Title  ",
            "123456789",
        ],
    )
    def test_accepts_supported_forms(self, downloader, url):
        assert downloader.extract_document_id(url) == "123456789"

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            None,
            "not a url",
            "https://www.scribd.com/",
            "https://www.scribd.com/document/abc/Title",
            "https://scribd.com.evil.example/document/123/Title",
            "https://notscribd.com/document/123456789/Title",
            "https://example.com/document/123456789/Title",
        ],
    )
    def test_rejects_unsupported_forms(self, downloader, url):
        assert downloader.extract_document_id(url) is None

    def test_query_string_is_ignored(self, downloader):
        url = "https://www.scribd.com/document/123456789/Title?campaign=x#page=2"
        assert downloader.extract_document_id(url) == "123456789"


class TestBuildEmbedUrl:
    def test_builds_content_url(self, downloader):
        assert (
            downloader.build_embed_url("123456789")
            == "https://www.scribd.com/embeds/123456789/content"
        )


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Simple-Title", "Simple-Title"),
            ("With Spaces", "With Spaces"),
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32", "system32"),
            ("a<b>c:d|e?f*g", "abcdefg"),
            ("trailing dots...", "trailing dots"),
            ("  padded  ", "padded"),
        ],
    )
    def test_cleans_dangerous_input(self, downloader, raw, expected):
        assert downloader.sanitize_filename(raw, "fallback") == expected

    @pytest.mark.parametrize("raw", ["", "   ", "...", "///", None, "<<>>"])
    def test_falls_back_when_nothing_survives(self, downloader, raw):
        assert downloader.sanitize_filename(raw, "fallback") == "fallback"

    def test_windows_reserved_names_get_suffix(self, downloader):
        assert downloader.sanitize_filename("CON", "fallback") == "CON_"
        assert downloader.sanitize_filename("lpt1", "fallback") == "lpt1_"

    def test_length_is_bounded(self, downloader):
        assert len(downloader.sanitize_filename("x" * 500, "fallback")) == 180

    def test_percent_encoding_is_decoded(self, downloader):
        assert downloader.sanitize_filename("A%20B", "fallback") == "A B"


class TestDefaultOutputFilename:
    def test_uses_last_path_segment(self, downloader):
        url = "https://www.scribd.com/document/123456789/My-Document"
        assert downloader.default_output_filename(url, "123456789") == (
            "My-Document.pdf"
        )

    def test_falls_back_to_document_id_when_no_title(self, downloader):
        url = "https://www.scribd.com/document/123456789"
        assert downloader.default_output_filename(url, "123456789") == (
            "scribd-123456789.pdf"
        )

    def test_falls_back_for_bare_id_input(self, downloader):
        assert downloader.default_output_filename("123456789", "123456789") == (
            "scribd-123456789.pdf"
        )

    def test_handles_scheme_less_url(self, downloader):
        url = "www.scribd.com/document/123456789/My-Document"
        assert downloader.default_output_filename(url, "123456789") == (
            "My-Document.pdf"
        )

    def test_traversal_segment_cannot_escape_directory(self, downloader):
        url = "https://www.scribd.com/document/123456789/..%2F..%2Fpasswd"
        filename = downloader.default_output_filename(url, "123456789")
        assert "/" not in filename and "\\" not in filename
        assert filename == "passwd.pdf"


class TestParsePageSelection:
    def test_none_selects_every_page(self, downloader):
        assert downloader.parse_page_selection(None, 4) == [1, 2, 3, 4]

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("3", [3]),
            ("1-3", [1, 2, 3]),
            ("1-2,5", [1, 2, 5]),
            ("5,1-2", [1, 2, 5]),
            ("1-3,2-4", [1, 2, 3, 4]),
            (" 1 - 3 , 6 ", [1, 2, 3, 6]),
            ("1-99", [1, 2, 3, 4, 5, 6]),
        ],
    )
    def test_parses_valid_specs(self, downloader, spec, expected):
        assert downloader.parse_page_selection(spec, 6) == expected

    @pytest.mark.parametrize(
        "spec", ["0", "0-2", "3-1", "abc", "1-", "-3", "1..3", ",", "9", "7-9"]
    )
    def test_rejects_invalid_specs(self, downloader, spec):
        with pytest.raises(ValueError):
            downloader.parse_page_selection(spec, 6)


class TestChunked:
    def test_splits_into_batches(self, downloader):
        assert list(downloader.chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]

    def test_empty_input_yields_nothing(self, downloader):
        assert list(downloader.chunked([], 3)) == []

    def test_batch_larger_than_input(self, downloader):
        assert list(downloader.chunked([1, 2], 10)) == [[1, 2]]


class TestEnvHelpers:
    def test_env_int_reads_value(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_TEST_INT", "42")
        assert downloader.env_int("SCRIBD_TEST_INT", 7) == 42

    def test_env_int_uses_default_when_unset_or_blank(self, downloader, monkeypatch):
        monkeypatch.delenv("SCRIBD_TEST_INT", raising=False)
        assert downloader.env_int("SCRIBD_TEST_INT", 7) == 7
        monkeypatch.setenv("SCRIBD_TEST_INT", "  ")
        assert downloader.env_int("SCRIBD_TEST_INT", 7) == 7

    def test_env_int_survives_garbage(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_TEST_INT", "not-a-number")
        assert downloader.env_int("SCRIBD_TEST_INT", 7) == 7

    def test_env_int_clamps_to_minimum(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_TEST_INT", "0")
        assert downloader.env_int("SCRIBD_TEST_INT", 8, minimum=1) == 1

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off"])
    def test_env_flag_false_values(self, downloader, monkeypatch, raw):
        monkeypatch.setenv("SCRIBD_TEST_FLAG", raw)
        assert downloader.env_flag("SCRIBD_TEST_FLAG", True) is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "anything"])
    def test_env_flag_true_values(self, downloader, monkeypatch, raw):
        monkeypatch.setenv("SCRIBD_TEST_FLAG", raw)
        assert downloader.env_flag("SCRIBD_TEST_FLAG", False) is True


class TestExportSettings:
    def test_cli_flags_win_over_environment(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_EXPORT_BATCH_SIZE", "16")
        args = downloader.build_argument_parser().parse_args(
            ["123456789", "--batch-size", "3"]
        )
        assert downloader.ExportSettings.from_args(args).export_batch_size == 3

    def test_environment_supplies_defaults(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_EXPORT_BATCH_SIZE", "16")
        monkeypatch.setenv("SCRIBD_CDP_TIMEOUT", "900")
        monkeypatch.setenv("SCRIBD_HEADLESS", "0")
        args = downloader.build_argument_parser().parse_args(["123456789"])
        settings = downloader.ExportSettings.from_args(args)
        assert settings.export_batch_size == 16
        assert settings.cdp_timeout_seconds == 900
        assert settings.headless is False

    def test_no_headless_flag_overrides_environment(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_HEADLESS", "1")
        args = downloader.build_argument_parser().parse_args(
            ["123456789", "--no-headless"]
        )
        assert downloader.ExportSettings.from_args(args).headless is False


class TestResolveOutputPath:
    def _args(self, downloader, argv):
        return downloader.build_argument_parser().parse_args(argv)

    def test_defaults_to_cwd(self, downloader, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = self._args(downloader, ["123456789"])
        url = "https://www.scribd.com/document/123456789/Title"
        assert downloader.resolve_output_path(args, url, "123456789") == str(
            tmp_path / "Title.pdf"
        )

    def test_directory_output_appends_filename(self, downloader, tmp_path):
        target = tmp_path / "out"
        target.mkdir()
        args = self._args(downloader, ["123456789", "-o", str(target)])
        url = "https://www.scribd.com/document/123456789/Title"
        assert downloader.resolve_output_path(args, url, "123456789") == str(
            target / "Title.pdf"
        )

    def test_existing_file_refused_without_force(self, downloader, tmp_path):
        target = tmp_path / "Title.pdf"
        target.write_bytes(b"existing")
        args = self._args(downloader, ["123456789", "-o", str(target)])
        with pytest.raises(RuntimeError, match="already exists"):
            downloader.resolve_output_path(args, "123456789", "123456789")

    def test_force_allows_overwrite(self, downloader, tmp_path):
        target = tmp_path / "Title.pdf"
        target.write_bytes(b"existing")
        args = self._args(downloader, ["123456789", "-o", str(target), "--force"])
        assert downloader.resolve_output_path(args, "123456789", "123456789") == str(
            target
        )

    def test_missing_parent_directory_is_created(self, downloader, tmp_path):
        target = tmp_path / "nested" / "deeper" / "Title.pdf"
        args = self._args(downloader, ["123456789", "-o", str(target)])
        assert downloader.resolve_output_path(args, "123456789", "123456789") == str(
            target
        )
        assert target.parent.is_dir()


class TestBrowserCandidatePaths:
    def test_windows_uses_program_files_and_local_appdata(
        self, downloader, monkeypatch
    ):
        monkeypatch.setattr(downloader, "windows_registry_browser_paths", lambda _: [])
        monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
        monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")

        paths = downloader.browser_candidate_paths("brave", platform_name="win32")

        assert any("Program Files" in path for path in paths)
        assert any("AppData" in path for path in paths)
        assert all(path.endswith("brave.exe") for path in paths)

    def test_windows_registry_hits_come_first(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader,
            "windows_registry_browser_paths",
            lambda _: [r"D:\Custom\brave.exe"],
        )
        monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        paths = downloader.browser_candidate_paths("brave", platform_name="win32")

        assert paths[0] == r"D:\Custom\brave.exe"

    def test_windows_skips_missing_environment_roots(self, downloader, monkeypatch):
        monkeypatch.setattr(downloader, "windows_registry_browser_paths", lambda _: [])
        monkeypatch.delenv("ProgramFiles", raising=False)
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        assert downloader.browser_candidate_paths("chrome", platform_name="win32") == []

    def test_macos_includes_user_applications(self, downloader):
        paths = downloader.browser_candidate_paths("chrome", platform_name="darwin")

        assert "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" in paths
        assert any(not path.startswith("/Applications") for path in paths)

    def test_linux_resolves_names_on_path(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader.shutil,
            "which",
            lambda name: "/usr/bin/brave-browser" if name == "brave-browser" else None,
        )

        paths = downloader.browser_candidate_paths("brave", platform_name="linux")

        assert "/usr/bin/brave-browser" in paths
        assert "/opt/brave.com/brave/brave" in paths

    def test_duplicates_are_dropped(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader.shutil, "which", lambda name: "/opt/brave.com/brave/brave"
        )

        paths = downloader.browser_candidate_paths("brave", platform_name="linux")

        assert len(paths) == len(set(paths))


class TestResolveBrowser:
    def test_explicit_path_wins(self, downloader, tmp_path):
        binary = tmp_path / "brave.exe"
        binary.write_text("")

        label, path = downloader.resolve_browser("brave", str(binary))

        assert label == "Brave"
        assert path == str(binary)

    def test_explicit_path_must_exist(self, downloader, tmp_path):
        missing = tmp_path / "nope.exe"

        with pytest.raises(RuntimeError, match="not found"):
            downloader.resolve_browser("brave", str(missing))

    def test_explicit_path_with_auto_is_labelled_custom(self, downloader, tmp_path):
        binary = tmp_path / "some-browser"
        binary.write_text("")

        label, _ = downloader.resolve_browser("auto", str(binary))

        assert label == "Custom browser"

    def test_auto_prefers_chrome(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader,
            "find_browser_binary",
            lambda key, platform_name=None: f"/bin/{key}",
        )

        assert downloader.resolve_browser("auto") == ("Google Chrome", "/bin/chrome")

    def test_auto_falls_through_to_brave(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader,
            "find_browser_binary",
            lambda key, platform_name=None: "/bin/brave" if key == "brave" else None,
        )

        assert downloader.resolve_browser("auto") == ("Brave", "/bin/brave")

    def test_auto_falls_back_to_chromedriver_default(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader, "find_browser_binary", lambda key, platform_name=None: None
        )

        label, path = downloader.resolve_browser("auto")

        assert path is None
        assert label == "ChromeDriver default"

    def test_named_browser_missing_is_an_error(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader, "find_browser_binary", lambda key, platform_name=None: None
        )
        monkeypatch.setattr(
            downloader,
            "browser_candidate_paths",
            lambda key, platform_name=None: [r"C:\Program Files\brave.exe"],
        )

        with pytest.raises(RuntimeError) as excinfo:
            downloader.resolve_browser("brave")

        message = str(excinfo.value)
        assert "Brave was not found" in message
        assert r"C:\Program Files\brave.exe" in message
        assert "--browser-path" in message

    def test_named_browser_found(self, downloader, monkeypatch):
        monkeypatch.setattr(
            downloader,
            "find_browser_binary",
            lambda key, platform_name=None: "/opt/chromium",
        )

        assert downloader.resolve_browser("chromium") == ("Chromium", "/opt/chromium")


class TestBuildBrowserOptions:
    def test_binary_location_is_set(self, downloader, tmp_path):
        options = downloader.build_browser_options(str(tmp_path), True, "/bin/brave")

        assert options.binary_location == "/bin/brave"

    def test_binary_location_left_alone_without_a_path(self, downloader, tmp_path):
        options = downloader.build_browser_options(str(tmp_path), True, None)

        assert options.binary_location == ""

    def test_headless_flag_follows_the_setting(self, downloader, tmp_path):
        headless = downloader.build_browser_options(str(tmp_path), True)
        windowed = downloader.build_browser_options(str(tmp_path), False)

        assert "--headless=new" in headless.arguments
        assert "--headless=new" not in windowed.arguments

    def test_profile_directory_is_passed_through(self, downloader, tmp_path):
        options = downloader.build_browser_options(str(tmp_path), True)

        assert f"--user-data-dir={tmp_path}" in options.arguments


class TestBrowserSettings:
    def test_defaults_to_auto(self, downloader, monkeypatch):
        monkeypatch.delenv("SCRIBD_BROWSER", raising=False)
        monkeypatch.delenv("SCRIBD_BROWSER_PATH", raising=False)
        args = downloader.build_argument_parser().parse_args(["123456789"])
        settings = downloader.ExportSettings.from_args(args)

        assert settings.browser == "auto"
        assert settings.browser_path is None

    def test_environment_supplies_browser(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_BROWSER", "brave")
        monkeypatch.setenv("SCRIBD_BROWSER_PATH", r"C:\brave.exe")
        args = downloader.build_argument_parser().parse_args(["123456789"])
        settings = downloader.ExportSettings.from_args(args)

        assert settings.browser == "brave"
        assert settings.browser_path == r"C:\brave.exe"

    def test_flags_win_over_environment(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_BROWSER", "chromium")
        args = downloader.build_argument_parser().parse_args(
            ["123456789", "--browser", "brave"]
        )

        assert downloader.ExportSettings.from_args(args).browser == "brave"

    def test_bad_environment_value_falls_back(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_BROWSER", "firefox")
        args = downloader.build_argument_parser().parse_args(["123456789"])

        assert downloader.ExportSettings.from_args(args).browser == "auto"

    def test_parser_rejects_unknown_browser(self, downloader):
        with pytest.raises(SystemExit):
            downloader.build_argument_parser().parse_args(
                ["123456789", "--browser", "firefox"]
            )


class TestEnvChoice:
    def test_returns_allowed_value(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_TEST_CHOICE", "brave")
        assert downloader.env_choice("SCRIBD_TEST_CHOICE", "auto", ("auto", "brave")) == (
            "brave"
        )

    def test_is_case_insensitive(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_TEST_CHOICE", "BRAVE")
        assert downloader.env_choice("SCRIBD_TEST_CHOICE", "auto", ("auto", "brave")) == (
            "brave"
        )

    def test_unknown_value_falls_back(self, downloader, monkeypatch):
        monkeypatch.setenv("SCRIBD_TEST_CHOICE", "opera")
        assert downloader.env_choice("SCRIBD_TEST_CHOICE", "auto", ("auto", "brave")) == (
            "auto"
        )

    def test_unset_returns_default(self, downloader, monkeypatch):
        monkeypatch.delenv("SCRIBD_TEST_CHOICE", raising=False)
        assert downloader.env_choice("SCRIBD_TEST_CHOICE", "auto", ("auto",)) == "auto"


class TestArgumentParser:
    def test_url_is_optional(self, downloader):
        assert downloader.build_argument_parser().parse_args([]).url is None

    def test_parses_all_flags(self, downloader):
        args = downloader.build_argument_parser().parse_args(
            [
                "https://www.scribd.com/document/1/Title",
                "-o",
                "out.pdf",
                "--force",
                "--pages",
                "1-5",
                "--batch-size",
                "4",
                "--timeout",
                "900",
                "--page-timeout",
                "180",
                "--no-headless",
                "--browser",
                "brave",
                "--browser-path",
                r"C:\brave.exe",
            ]
        )
        assert args.browser == "brave"
        assert args.browser_path == r"C:\brave.exe"
        assert args.output == "out.pdf"
        assert args.force is True
        assert args.pages == "1-5"
        assert args.batch_size == 4
        assert args.timeout == 900
        assert args.page_timeout == 180
        assert args.no_headless is True
