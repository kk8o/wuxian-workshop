"""What the build takes from Windows (installer/runtimes.py) and the doctor's checks for it; no real download, nothing
installed: the opener, the signature check and the installer process are stand-ins."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.installer import fake
from wuxianworkshop.installer import doctor as D
from wuxianworkshop.installer import runtimes as R


class Opener:
    """urlopen for one fake download"""

    def __init__(self, data):
        self.data, self.urls = data, []

    def __call__(self, url):
        self.urls.append(url)
        return io.BytesIO(self.data)


class Download(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_download_to_the_folder(self):
        opener = Opener(b"MZ" + bytes(5000))
        path = R.download("webview2", self.folder, opener)
        self.assertEqual(path, self.folder / "MicrosoftEdgeWebview2Setup.exe")
        self.assertEqual(path.read_bytes()[:2], b"MZ")
        self.assertEqual(opener.urls, [R.RUNTIMES["webview2"].url])
        self.assertEqual([p.name for p in self.folder.iterdir()], [path.name])        # no .part left

    def test_too_big_is_not_an_installer(self):
        with mock.patch.object(R, "MAX_DOWNLOAD", 1000), self.assertRaises(R.InstallError):
            R.download("dotnet", self.folder, Opener(bytes(5000)))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_network_error(self):
        def opener(url):
            raise OSError("no route")
        with self.assertRaisesRegex(R.InstallError, "could not download ndp48-web.exe"):
            R.download("dotnet", self.folder, opener)

    def test_only_microsofts_signature_is_started(self):
        path = R.download("webview2", self.folder, Opener(b"MZ"))
        with mock.patch.object(R, "signature", return_value=("Valid", "CN=Someone Else, O=Someone Else")), \
                self.assertRaisesRegex(R.InstallError, "not Microsoft's"):
            R.verify(path)
        with mock.patch.object(R, "signature", return_value=("HashMismatch", "CN=Microsoft Corporation, O=Microsoft Corporation")), \
                self.assertRaises(R.InstallError):
            R.verify(path)
        with mock.patch.object(R, "signature", return_value=("Valid", "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond")):
            R.verify(path)

    @unittest.skipUnless(os.name == "nt", "PowerShell's Get-AuthenticodeSignature")
    def test_signature_of_an_unsigned_file(self):
        path = self.folder / "plain.exe"
        path.write_bytes(b"MZ" + bytes(100))
        status, _ = R.signature(path)
        self.assertNotEqual(status, "Valid")

    def test_install_downloads_verifies_and_starts(self):
        proc = mock.Mock(pid=4321, wait=mock.Mock(return_value=0))
        with mock.patch.object(R, "signature", return_value=("Valid", "O=Microsoft Corporation")), \
                mock.patch.object(R.subprocess, "Popen", return_value=proc) as popen:
            r = R.install("webview2", wait=True, folder=self.folder, opener=Opener(b"MZ"))
        self.assertEqual((r["pid"], r["exit_code"]), (4321, 0))
        self.assertEqual(popen.call_args[0][0], [str(self.folder / "MicrosoftEdgeWebview2Setup.exe"), "/install"])
        with mock.patch.object(R, "signature", return_value=("NotSigned", "")), \
                mock.patch.object(R.subprocess, "Popen") as popen, self.assertRaises(R.InstallError):
            R.install("dotnet", folder=self.folder, opener=Opener(b"MZ"))
        popen.assert_not_called()                                 # an unsigned file is never run

    def test_dotnet_names(self):
        self.assertEqual(R.dotnet_name(533325), "4.8.1")
        self.assertEqual(R.dotnet_name(528040), "4.8")
        self.assertEqual(R.dotnet_name(461808), "4.7.2")
        self.assertEqual(R.dotnet_name(1), "4.x")


class DoctorChecks(unittest.TestCase):
    def checks(self, pv="154.0.4258.53", release=533325, build=19045):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}), \
                mock.patch.object(D, "webview2_version", return_value=pv), \
                mock.patch.object(D, "dotnet_release", return_value=release), \
                mock.patch.object(D, "windows_build", return_value=build):
            game = fake.make_client(tmp)
            return {c["id"]: c for c in D.run_checks(game, source=fake.make_source(tmp))}

    def test_all_there(self):
        c = self.checks()
        self.assertEqual((c["webview2"]["ok"], c["dotnet"]["ok"], c["windows"]["ok"]), (True, True, True))
        self.assertEqual(c["dotnet"]["detail"], "4.8.1（Release 值 533325）")

    def test_missing_ones_offer_an_install(self):
        c = self.checks(pv=None, release=394802, build=17763)
        self.assertEqual((c["webview2"]["ok"], c["webview2"]["fix"]), (False, "install_webview2"))
        self.assertEqual((c["dotnet"]["ok"], c["dotnet"]["fix"]), (False, "install_dotnet"))
        self.assertIn("4.6.2", c["dotnet"]["detail"])
        self.assertEqual((c["windows"]["ok"], c["windows"]["fix"]), (None, None))      # GDI then: nothing to install
        self.assertIn("GDI", c["windows"]["detail"])
        self.assertEqual(self.checks(release=None)["dotnet"]["fix"], "install_dotnet")

    def test_apply_fixes_starts_the_installers(self):
        checks = [dict(ok=False, fix="install_webview2"), dict(ok=False, fix="install_dotnet"), dict(ok=True, fix=None)]
        with mock.patch.object(R, "install") as install:
            self.assertEqual(D.apply_fixes(checks), ["install_dotnet", "install_webview2"])
        self.assertEqual([c[0][0] for c in install.call_args_list], ["dotnet", "webview2"])


if __name__ == "__main__":
    unittest.main()
