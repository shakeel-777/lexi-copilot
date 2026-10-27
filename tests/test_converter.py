import os
import sys
import unittest
from unittest import mock
from pathlib import Path

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import converter

class TestConverterImage(unittest.TestCase):
    @mock.patch("converter.Image.open")
    def test_convert_image_gray(self, mock_open):
        mock_img = mock.MagicMock()
        mock_open.return_value = mock_img
        mock_gray_img = mock.MagicMock()
        mock_img.convert.return_value = mock_gray_img

        result = converter.convert_image("test_image.png", "webp", option="gray")

        mock_open.assert_called_once_with(Path("test_image.png"))
        mock_img.convert.assert_called_once_with("L")
        expected_out = Path("test_image_gray.webp")
        mock_gray_img.save.assert_called_once_with(expected_out)
        self.assertEqual(result, str(expected_out))

    @mock.patch("converter.Image.open")
    def test_convert_image_half(self, mock_open):
        mock_img = mock.MagicMock()
        mock_open.return_value = mock_img
        mock_img.size = (100, 200)
        mock_resized_img = mock.MagicMock()
        mock_img.resize.return_value = mock_resized_img

        result = converter.convert_image("test_image.png", "webp", option="half")

        mock_open.assert_called_once_with(Path("test_image.png"))
        from PIL import Image
        mock_img.resize.assert_called_once_with((50, 100), Image.Resampling.LANCZOS)
        expected_out = Path("test_image_50.webp")
        mock_resized_img.save.assert_called_once_with(expected_out)
        self.assertEqual(result, str(expected_out))

    @mock.patch("converter.Image.open")
    def test_convert_image_pil_formats(self, mock_open):
        # target_ext = "webp"
        mock_img = mock.MagicMock()
        mock_open.return_value = mock_img

        result = converter.convert_image("test_image.png", "webp")

        expected_out = Path("test_image.webp")
        mock_img.save.assert_called_once_with(expected_out, "WEBP", quality=85)
        self.assertEqual(result, str(expected_out))

        # Test jpg conversion
        mock_img.reset_mock()
        mock_img.mode = "RGBA"
        mock_rgb_img = mock.MagicMock()
        mock_img.convert.return_value = mock_rgb_img

        result = converter.convert_image("test_image.png", "jpg")

        expected_out = Path("test_image.jpg")
        mock_img.convert.assert_called_once_with("RGB")
        mock_rgb_img.save.assert_called_once_with(expected_out, "JPEG", quality=90)
        self.assertEqual(result, str(expected_out))

        # Test pdf conversion
        mock_img.reset_mock()
        mock_img.mode = "RGBA"
        mock_rgb_img = mock.MagicMock()
        mock_img.convert.return_value = mock_rgb_img

        result = converter.convert_image("test_image.png", "pdf")

        expected_out = Path("test_image.pdf")
        mock_img.convert.assert_called_once_with("RGB")
        mock_rgb_img.save.assert_called_once_with(expected_out, "PDF", resolution=150.0)
        self.assertEqual(result, str(expected_out))

    @mock.patch("converter.subprocess.run")
    @mock.patch("converter.shutil.which")
    def test_convert_image_imagemagick(self, mock_which, mock_run):
        def which_side_effect(name):
            if name == "magick": return True
            return False
        mock_which.side_effect = which_side_effect

        # Format not natively handled by PIL (e.g., something that drops through or "ico")
        result = converter.convert_image("test_image.png", "ico")

        expected_out = Path("test_image.ico")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "magick")
        self.assertEqual(args[1], str(Path("test_image.png")))
        self.assertEqual(args[2], "-define")
        self.assertEqual(args[3], "icon:auto-resize=256,128,64,48,32,16")
        self.assertEqual(args[4], str(expected_out))
        self.assertEqual(result, str(expected_out))

        mock_run.reset_mock()
        # Test normal imagemagick conversion for some format not natively handled
        result = converter.convert_image("test_image.png", "avif")

        expected_out = Path("test_image.avif")
        mock_run.assert_called_once_with(["magick", str(Path("test_image.png")), str(expected_out)], check=True)
        self.assertEqual(result, str(expected_out))

    @mock.patch("converter.Image.open")
    @mock.patch("converter.shutil.which")
    def test_convert_image_fallback(self, mock_which, mock_open):
        mock_which.return_value = False
        mock_img = mock.MagicMock()
        mock_open.return_value = mock_img

        result = converter.convert_image("test_image.png", "unknown")

        expected_out = Path("test_image.unknown")
        mock_img.save.assert_called_once_with(expected_out)
        self.assertEqual(result, str(expected_out))


class TestConverterOther(unittest.TestCase):
    @mock.patch("converter.subprocess.run")
    def test_convert_pdf_pages(self, mock_run):
        # We need to patch glob.glob to simulate pdftoppm output
        with mock.patch("converter.glob.glob") as mock_glob:
            mock_glob.return_value = ["test_page-1.png", "test_page-2.png"]
            result = converter.convert_pdf("test.pdf", "png", option="pages")

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertEqual(args[0], "pdftoppm")
            self.assertEqual(args[1], "-png")
            self.assertEqual(args[2], "-r")
            self.assertEqual(args[3], "150")
            self.assertEqual(args[4], "test.pdf")
            self.assertTrue(args[5].endswith("test_page"))

            self.assertEqual(result, "test_page-1.png")

    @mock.patch("converter.subprocess.run")
    @mock.patch("converter.shutil.which")
    def test_convert_pdf_txt(self, mock_which, mock_run):
        # pdftotext is available
        mock_which.return_value = True

        result = converter.convert_pdf("test.pdf", "txt")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "pdftotext")
        self.assertEqual(args[1], "test.pdf")
        self.assertTrue(args[2].endswith("test.txt"))

        # pdftotext not available, use libreoffice
        mock_which.return_value = False
        mock_run.reset_mock()
        result = converter.convert_pdf("test.pdf", "txt")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "libreoffice")
        self.assertEqual(args[1], "--headless")
        self.assertEqual(args[2], "--convert-to")
        self.assertEqual(args[3], "txt:Text")

    @mock.patch("converter.subprocess.run")
    @mock.patch("converter.Path.exists")
    def test_convert_pdf_docx(self, mock_exists, mock_run):
        mock_exists.return_value = True
        result = converter.convert_pdf("test.pdf", "docx")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "libreoffice")
        self.assertEqual(args[1], "--headless")
        self.assertEqual(args[2], "--convert-to")
        self.assertEqual(args[3], "docx")
        self.assertTrue(result.endswith("test.docx"))

    @mock.patch("converter.subprocess.run")
    @mock.patch("converter.shutil.which")
    def test_convert_pdf_compress(self, mock_which, mock_run):
        mock_which.return_value = True
        result = converter.convert_pdf("test.pdf", "pdf", option="compress")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "gs")
        self.assertTrue(result.endswith("test_compressed.pdf"))

    @mock.patch("converter.subprocess.run")
    @mock.patch("converter.shutil.which")
    @mock.patch("converter.Path.exists")
    def test_convert_document(self, mock_exists, mock_which, mock_run):
        mock_which.return_value = True
        mock_exists.return_value = True

        result = converter.convert_document("test.docx", "pdf")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "libreoffice")
        self.assertEqual(args[3], "pdf")
        self.assertTrue(result.endswith("test.pdf"))

    @mock.patch("converter.csv.DictReader")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("converter.json.dump")
    def test_convert_spreadsheet_csv_to_json(self, mock_json, mock_open, mock_reader):
        mock_reader.return_value = [{"a": "1", "b": "2"}]
        result = converter.convert_spreadsheet("test.csv", "json")

        self.assertTrue(result.endswith("test.json"))
        mock_json.assert_called_once()
        args = mock_json.call_args[0]
        self.assertEqual(args[0], [{"a": "1", "b": "2"}])

    @mock.patch("converter.subprocess.run")
    @mock.patch("converter.shutil.which")
    @mock.patch("converter.Path.exists")
    def test_convert_spreadsheet_libreoffice(self, mock_exists, mock_which, mock_run):
        mock_which.return_value = True
        mock_exists.return_value = True

        result = converter.convert_spreadsheet("test.xlsx", "pdf")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "libreoffice")
        self.assertEqual(args[3], "pdf")
        self.assertTrue(result.endswith("test.pdf"))

    @mock.patch("converter.subprocess.run")
    @mock.patch("converter.shutil.which")
    def test_convert_media(self, mock_which, mock_run):
        mock_which.return_value = True

        # Test mp3
        result = converter.convert_media("test.mp4", "mp3")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "ffmpeg")
        self.assertEqual(args[1], "-y")
        self.assertIn("-vn", args)
        self.assertTrue(result.endswith("test.mp3"))

        mock_run.reset_mock()
        # Test gif
        result = converter.convert_media("test.mp4", "gif")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "ffmpeg")
        self.assertEqual(args[1], "-y")
        self.assertIn("-vf", args)
        self.assertTrue(result.endswith("test.gif"))

        mock_run.reset_mock()
        # Test general
        result = converter.convert_media("test.avi", "mp4")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "ffmpeg")
        self.assertEqual(args[1], "-y")
        self.assertIn("libx264", args)
        self.assertTrue(result.endswith("test.mp4"))

    @mock.patch("converter.zipfile.ZipFile")
    @mock.patch("converter.shutil.make_archive")
    @mock.patch("converter.Path.is_dir")
    def test_convert_archive_zip(self, mock_isdir, mock_makearchive, mock_zipfile):
        # File to zip
        mock_isdir.return_value = False
        result = converter.convert_archive("test.txt", "zip")
        self.assertTrue(result.endswith("test.zip"))
        mock_zipfile.assert_called_once()

        # Dir to zip
        mock_isdir.return_value = True
        result = converter.convert_archive("testdir", "zip")
        self.assertTrue(result.endswith("testdir.zip"))
        mock_makearchive.assert_called_once()

    @mock.patch("converter.tarfile.open")
    def test_convert_archive_tar_gz(self, mock_taropen):
        result = converter.convert_archive("test.txt", "tar.gz")
        self.assertTrue(result.endswith("test.tar.gz"))
        mock_taropen.assert_called_once()

    @mock.patch("converter.zipfile.ZipFile")
    @mock.patch("converter.Path.mkdir")
    def test_convert_archive_extract(self, mock_mkdir, mock_zipfile):
        result = converter.convert_archive("test.zip", "folder", option="extract")
        self.assertTrue(result.endswith("test_extracted"))
        mock_mkdir.assert_called_once()
        mock_zipfile.assert_called_once()

if __name__ == "__main__":
    unittest.main()
