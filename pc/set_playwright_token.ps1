# Saves the Playwright MCP Bridge extension token (from the extension's page in Edge) as a user
# environment variable, so the Playwright MCP server connects to Edge without asking "Allow?" each time.
# The token is typed hidden and never printed.
$secure = Read-Host "Paste the Playwright extension token (hidden), then Enter" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if (-not $token) { Write-Host "Nothing pasted, nothing saved."; exit 1 }
# the extension shows "PLAYWRIGHT_MCP_EXTENSION_TOKEN=<token>": keep only the token
$token = ($token.Trim() -replace '^PLAYWRIGHT_MCP_EXTENSION_TOKEN=', '').Trim()
[Environment]::SetEnvironmentVariable("PLAYWRIGHT_MCP_EXTENSION_TOKEN", $token, "User")
Write-Host "Saved ($($token.Length) characters). If the Playwright MCP server is running, close it:"
Write-Host "the PC runner starts it again with the token on its next cycle."
