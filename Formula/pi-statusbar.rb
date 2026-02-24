class PiStatusbar < Formula
  desc "Pi macOS status bar app with local daemon and session controls"
  homepage "https://github.com/jademind/pi-statusbar"
  url "https://github.com/jademind/pi-statusbar/archive/refs/tags/v0.1.3.tar.gz"
  sha256 "22e8445c2ad0c4e470ee7c9d74c42f0d3b19a0fd7e3b1f68164030a98bc9d895"
  version "0.1.3"
  license "MIT"
  head "https://github.com/jademind/pi-statusbar.git", branch: "main"

  depends_on :macos
  depends_on "python@3.12"
  depends_on "swift" => :build

  def install
    system "swift", "build", "-c", "release"

    libexec.install Dir["*"]

    bin.install ".build/release/PiStatusBar"
    (bin/"statusdctl").write_env_script libexec/"daemon/statusdctl", PI_STATUSBAR_ROOT: libexec
    (bin/"statusd-service").write_env_script libexec/"daemon/statusd-service", PI_STATUSBAR_ROOT: libexec
  end

  service do
    run [
      Formula["python@3.12"].opt_bin/"python3",
      opt_libexec/"daemon/pi_statusd.py"
    ]
    keep_alive true
    run_type :immediate
    working_dir var
    log_path var/"log/pi-statusd.log"
    error_log_path var/"log/pi-statusd.log"
  end

  test do
    output = shell_output("#{bin}/statusdctl status 2>&1", 1)
    assert_match "pi-statusd", output
  end
end
