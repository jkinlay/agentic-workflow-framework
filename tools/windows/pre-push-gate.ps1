<#
pre-push-gate.ps1 (DevHead 18:40, 1.9.5 standard): run before EVERY sync/batch push.
  0. AWF-99 integrity check: refuse an empty lane diff or deletion of a test file/function/It block that existed at the
     merge base or any reachable branch commit since it, unless every deletion has an exact-head owner ruling;
  1. installer release check: agentic.installer.verify_release on the source checkout (allow_source_checkout=True), i.e. MANIFEST.json
     membership + every per-file digest, exactly what install/upgrade does first;
  2. the 1.9.4-upgrade test: python -B -m unittest test_upgrade_194 from .agentic/tests (0 tests collected = failure).
Preconditions: worktree HEAD == -Commit (when given) and no tracked modifications. Exit 0 = PASS; 1 = release check FAIL; 2 = upgrade test FAIL; 3 = precondition FAIL; 4 = actionlint FAIL (workflow change; gate-actionlint.ps1, DevHead 00:39 (3)); 5 = standalone-import FAIL (import-isolation-check.py, replica of CI test_module_import_isolation from #124/AWF-77; Jonathan via DevHead 11:09); 6 = pre-push integrity FAIL (AWF-99); 7 = Python collector cross-check FAIL (AWF-116).
#>
param(
  [Parameter(Mandatory)][string]$Worktree,
  [string]$Commit = '', [string]$Label = '',
  # Portable defaults: no machine-local absolute paths. Callers should pass -Py and
  # -ControllerRoot explicitly; otherwise Py resolves from PATH and ControllerRoot
  # falls back to Worktree (which is already caller-supplied).
  [string]$Py = '',
  [string]$ControllerRoot = '',
  [string]$BaseCommit = '', [string]$Ticket = '', [string]$RulingsPath = '',
  [string]$PipelineLog = '', [string]$StatusPath = '', [string]$AlertsPath = ''
)
$ErrorActionPreference = 'Continue'
if (-not $ControllerRoot) { $ControllerRoot = $Worktree }
if (-not $Py) {
  $cmd = Get-Command -Name python -ErrorAction SilentlyContinue
  if ($null -eq $cmd) { Write-Error 'pre-push-gate.ps1: -Py is required when python is not on PATH'; exit 3 }
  $Py = $cmd.Source
}
$W = $ControllerRoot; $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Force "$W\gate-logs" | Out-Null; $log = "$W\gate-logs\gate-$ts-$((Split-Path $Worktree -Leaf) -replace '[^\w-]','_').log"
function Out($m) { $m; "$(Get-Date -Format o) $m" | Out-File -Append -Encoding utf8 $log }
function Done($code, $m) { Out "GATE $(if ($code -eq 0) { 'PASS' } else { 'FAIL' }) ($Label): $m [log $log]"; "$(Get-Date -Format o)`t$Label`t$Worktree`t$(git -C $Worktree rev-parse HEAD 2>$null)`t$(if ($code -eq 0) { 'PASS' } else { "FAIL:$code" })`t$m" | Out-File -Append -Encoding utf8 "$W\gate-logs\GATE-RUNS.tsv"; exit $code }

function Record-IntegrityBlock([string]$Message, [string]$Head) {
  $record = "$(Get-Date -Format o) $Message"
  if ($PipelineLog) { $record | Out-File -Append -Encoding utf8 -LiteralPath $PipelineLog }
  if ($AlertsPath) { "$(Get-Date -Format o) $Ticket $Message" | Out-File -Append -Encoding utf8 -LiteralPath $AlertsPath }
  if ($StatusPath) {
    $status = $null
    if (Test-Path -LiteralPath $StatusPath) { try { $status = Get-Content -Raw -LiteralPath $StatusPath | ConvertFrom-Json } catch {} }
    if ($null -eq $status) { $status = [pscustomobject]@{} }
    $status | Add-Member -NotePropertyName ticket -NotePropertyValue $Ticket -Force
    $status | Add-Member -NotePropertyName phase -NotePropertyValue 'blocked-prepush-integrity' -Force
    $status | Add-Member -NotePropertyName head -NotePropertyValue $Head -Force
    $status | Add-Member -NotePropertyName updated -NotePropertyValue (Get-Date -Format o) -Force
    $status | Add-Member -NotePropertyName note -NotePropertyValue $Message -Force
    $status | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 -LiteralPath $StatusPath
  }
}

function Test-IsTestFile([string]$Path) {
  $p = $Path -replace '\\','/'
  $leaf = Split-Path $p -Leaf
  $recognizedExtension = $leaf -match '(?i)(\.py$|\.ps1$|\.(js|jsx|ts|tsx|mjs|cjs)$)'
  if ($p -match '(^|/)(tests?|specs?)/' -and $recognizedExtension) { return $true }
  return [bool]($leaf -match '(?i)(\.Tests\.ps1$|^test-.+\.ps1$|(\.test|\.spec)\.(js|jsx|ts|tsx|mjs|cjs)$|^test_.*\.py$|_test\.py$)')
}

function Get-PesterCommandKind($Command) {
  $commandName = "$($Command.GetCommandName())"
  if ($commandName -notmatch '(?i)^(?:Pester\\)?(It|Describe|Context)$') { return '' }
  switch ($matches[1].ToLowerInvariant()) {
    'it' { return 'It' }
    'describe' { return 'Describe' }
    'context' { return 'Context' }
  }
  return ''
}

function Get-PesterStaticAstValue($Expression) {
  while ($Expression -is [System.Management.Automation.Language.CommandExpressionAst]) { $Expression = $Expression.Expression }
  if ($Expression -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
      $Expression -is [System.Management.Automation.Language.ConstantExpressionAst]) {
    return [pscustomobject]@{ Resolved=$true; Value="$($Expression.Value)" }
  }
  if ($Expression -is [System.Management.Automation.Language.ExpandableStringExpressionAst] -and
      @($Expression.NestedExpressions).Count -eq 0) {
    return [pscustomobject]@{ Resolved=$true; Value="$($Expression.Value)" }
  }
  return [pscustomobject]@{ Resolved=$false; Value='' }
}

function Resolve-PesterCommandLabel($Command, $RootAst) {
  $labelElement = @($Command.CommandElements | Select-Object -Skip 1 | Where-Object {
    $_ -isnot [System.Management.Automation.Language.CommandParameterAst] -and
    $_ -isnot [System.Management.Automation.Language.ScriptBlockExpressionAst]
  } | Select-Object -First 1)
  if (-not $labelElement.Count) { return [pscustomobject]@{ Resolved=$false; Value='' } }

  $literal = Get-PesterStaticAstValue $labelElement[0]
  if ($literal.Resolved) { return $literal }
  if ($labelElement[0] -isnot [System.Management.Automation.Language.VariableExpressionAst]) {
    return [pscustomobject]@{ Resolved=$false; Value='' }
  }

  $variableName = "$($labelElement[0].VariablePath.UserPath)"
  $assignments = @($RootAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left -is [System.Management.Automation.Language.VariableExpressionAst]
  }, $true) | Where-Object {
    "$($_.Left.VariablePath.UserPath)" -ieq $variableName -and
    $_.Parent -is [System.Management.Automation.Language.NamedBlockAst] -and
    $_.Parent.Parent -eq $RootAst -and
    $_.Extent.EndOffset -le $labelElement[0].Extent.StartOffset
  })
  if ($assignments.Count -ne 1) { return [pscustomobject]@{ Resolved=$false; Value='' } }
  return (Get-PesterStaticAstValue $assignments[0].Right)
}

function Split-PythonTopLevel([string]$Text, [char]$Delimiter = ',') {
  $parts = New-Object 'System.Collections.Generic.List[string]'
  $buffer = New-Object Text.StringBuilder
  $stack = New-Object 'System.Collections.Generic.List[char]'
  $quote = [char]0; $triple = $false; $escaped = $false
  for ($i = 0; $i -lt $Text.Length; $i++) {
    $ch = $Text[$i]
    if ($quote -ne [char]0) {
      [void]$buffer.Append($ch)
      if ($escaped) { $escaped = $false; continue }
      if ($ch -eq '\') { $escaped = $true; continue }
      if ($triple) {
        if ($ch -eq $quote -and $i + 2 -lt $Text.Length -and $Text[$i + 1] -eq $quote -and $Text[$i + 2] -eq $quote) {
          [void]$buffer.Append($quote); [void]$buffer.Append($quote); $i += 2; $quote = [char]0; $triple = $false
        }
      } elseif ($ch -eq $quote) { $quote = [char]0 }
      continue
    }
    if ($ch -eq "'" -or $ch -eq '"') {
      $quote = $ch
      $triple = ($i + 2 -lt $Text.Length -and $Text[$i + 1] -eq $ch -and $Text[$i + 2] -eq $ch)
      [void]$buffer.Append($ch)
      if ($triple) { [void]$buffer.Append($ch); [void]$buffer.Append($ch); $i += 2 }
      continue
    }
    if ($ch -eq '#') {
      while ($i -lt $Text.Length -and $Text[$i] -ne "`n") { $i++ }
      if ($i -lt $Text.Length) { [void]$buffer.Append("`n") }
      continue
    }
    if ($ch -eq '(' -or $ch -eq '[' -or $ch -eq '{') { $stack.Add($ch); [void]$buffer.Append($ch); continue }
    if ($ch -eq ')' -or $ch -eq ']' -or $ch -eq '}') {
      if (-not $stack.Count) { throw 'unexpected closing delimiter' }
      $open = $stack[$stack.Count - 1]
      $valid = (($open -eq '(' -and $ch -eq ')') -or ($open -eq '[' -and $ch -eq ']') -or ($open -eq '{' -and $ch -eq '}'))
      if (-not $valid) { throw 'mismatched delimiter' }
      $stack.RemoveAt($stack.Count - 1); [void]$buffer.Append($ch); continue
    }
    if ($ch -eq $Delimiter -and -not $stack.Count) { $parts.Add($buffer.ToString().Trim()); [void]$buffer.Clear(); continue }
    [void]$buffer.Append($ch)
  }
  if ($quote -ne [char]0 -or $stack.Count) { throw 'unterminated literal' }
  $last = $buffer.ToString().Trim()
  if ($last -or $parts.Count) { $parts.Add($last) }
  return @($parts)
}

function ConvertTo-PythonCanonical([string]$Text) {
  $buffer = New-Object Text.StringBuilder
  $quote = [char]0; $triple = $false; $escaped = $false
  for ($i = 0; $i -lt $Text.Length; $i++) {
    $ch = $Text[$i]
    if ($quote -ne [char]0) {
      [void]$buffer.Append($ch)
      if ($escaped) { $escaped = $false; continue }
      if ($ch -eq '\') { $escaped = $true; continue }
      if ($triple) {
        if ($ch -eq $quote -and $i + 2 -lt $Text.Length -and $Text[$i + 1] -eq $quote -and $Text[$i + 2] -eq $quote) {
          [void]$buffer.Append($quote); [void]$buffer.Append($quote); $i += 2; $quote = [char]0; $triple = $false
        }
      } elseif ($ch -eq $quote) { $quote = [char]0 }
      continue
    }
    if ($ch -eq "'" -or $ch -eq '"') {
      $quote = $ch
      $triple = ($i + 2 -lt $Text.Length -and $Text[$i + 1] -eq $ch -and $Text[$i + 2] -eq $ch)
      [void]$buffer.Append($ch)
      if ($triple) { [void]$buffer.Append($ch); [void]$buffer.Append($ch); $i += 2 }
      continue
    }
    if ($ch -eq '#') { while ($i -lt $Text.Length -and $Text[$i] -ne "`n") { $i++ }; continue }
    if (-not [char]::IsWhiteSpace($ch)) { [void]$buffer.Append($ch) }
  }
  if ($quote -ne [char]0) { throw 'unterminated string literal' }
  return $buffer.ToString()
}

function Test-PythonStringLiteral([string]$Text) {
  $value = $Text.Trim()
  $start = [regex]::Match($value, '^(?i:r|u|b|br|rb)?(?<quote>[\x27\x22])')
  if (-not $start.Success) { return $false }
  $quoteIndex = $start.Groups['quote'].Index
  $quote = $value[$quoteIndex]
  $triple = ($quoteIndex + 2 -lt $value.Length -and $value[$quoteIndex + 1] -eq $quote -and $value[$quoteIndex + 2] -eq $quote)
  $i = $quoteIndex + $(if ($triple) { 3 } else { 1 })
  $escaped = $false
  while ($i -lt $value.Length) {
    $ch = $value[$i]
    if ($escaped) { $escaped = $false; $i++; continue }
    if ($ch -eq '\') { $escaped = $true; $i++; continue }
    if ($triple) {
      if ($ch -eq $quote -and $i + 2 -lt $value.Length -and $value[$i + 1] -eq $quote -and $value[$i + 2] -eq $quote) {
        return ($i + 3 -eq $value.Length)
      }
    } elseif ($ch -eq $quote) {
      return ($i + 1 -eq $value.Length)
    }
    $i++
  }
  return $false
}

function Test-PythonLiteral([string]$Text) {
  $value = $Text.Trim()
  if (-not $value) { return $false }
  if ($value -match '^(?:None|True|False|Ellipsis)$') { return $true }
  if ($value -match '^[+-]?(?:(?:\d+(?:_\d+)*(?:\.\d*(?:_\d+)*)?|\.\d+(?:_\d+)*)(?:[eE][+-]?\d+(?:_\d+)*)?|0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+)[jJ]?$') { return $true }
  if (Test-PythonStringLiteral $value) { return $true }
  if ($value.Length -ge 2) {
    $open = $value[0]; $close = $value[$value.Length - 1]
    if (($open -eq '[' -and $close -eq ']') -or ($open -eq '(' -and $close -eq ')') -or ($open -eq '{' -and $close -eq '}')) {
      try { $items = @(Split-PythonTopLevel $value.Substring(1, $value.Length - 2)) } catch { return $false }
      foreach ($item in $items) {
        if (-not $item) { continue }
        if ($open -eq '{') {
          $pair = @(Split-PythonTopLevel $item ':')
          if ($pair.Count -gt 2) { return $false }
          foreach ($part in $pair) { if (-not (Test-PythonLiteral $part)) { return $false } }
          continue
        }
        if (-not (Test-PythonLiteral $item)) { return $false }
      }
      return $true
    }
  }
  return $false
}

function Remove-PythonGroupingParentheses([string]$Text) {
  $value = $Text.Trim()
  while ($value.Length -ge 2 -and $value[0] -eq '(' -and $value[$value.Length - 1] -eq ')') {
    try { $items = @(Split-PythonTopLevel $value.Substring(1, $value.Length - 2)) } catch { break }
    if ($items.Count -ne 1 -or -not $items[0]) { break }
    $value = $items[0].Trim()
  }
  return $value
}

function Get-PythonLiteralLabel([string]$Text) {
  $value = $Text.Trim()
  if ($value -match '^(?i:r|u|b|br|rb)?([\x27\x22])(.*)\1$') { return $matches[2] }
  return (ConvertTo-PythonCanonical $value)
}

function Get-PythonBalancedCall([string]$Content, [int]$OpenIndex) {
  $stack = New-Object 'System.Collections.Generic.List[char]'; $stack.Add('(')
  $quote = [char]0; $triple = $false; $escaped = $false
  for ($i = $OpenIndex + 1; $i -lt $Content.Length; $i++) {
    $ch = $Content[$i]
    if ($quote -ne [char]0) {
      if ($escaped) { $escaped = $false; continue }
      if ($ch -eq '\') { $escaped = $true; continue }
      if ($triple) {
        if ($ch -eq $quote -and $i + 2 -lt $Content.Length -and $Content[$i + 1] -eq $quote -and $Content[$i + 2] -eq $quote) { $i += 2; $quote = [char]0; $triple = $false }
      } elseif ($ch -eq $quote) { $quote = [char]0 }
      continue
    }
    if ($ch -eq "'" -or $ch -eq '"') {
      $quote = $ch; $triple = ($i + 2 -lt $Content.Length -and $Content[$i + 1] -eq $ch -and $Content[$i + 2] -eq $ch)
      if ($triple) { $i += 2 }; continue
    }
    if ($ch -eq '#') { while ($i -lt $Content.Length -and $Content[$i] -ne "`n") { $i++ }; continue }
    if ($ch -eq '(' -or $ch -eq '[' -or $ch -eq '{') { $stack.Add($ch); continue }
    if ($ch -eq ')' -or $ch -eq ']' -or $ch -eq '}') {
      if (-not $stack.Count) { return $null }
      $open = $stack[$stack.Count - 1]
      $valid = (($open -eq '(' -and $ch -eq ')') -or ($open -eq '[' -and $ch -eq ']') -or ($open -eq '{' -and $ch -eq '}'))
      if (-not $valid) { return $null }
      $stack.RemoveAt($stack.Count - 1)
      if (-not $stack.Count) { return [pscustomobject]@{ Inner=$Content.Substring($OpenIndex + 1, $i - $OpenIndex - 1); End=$i } }
    }
  }
  return $null
}

function Get-PytestCases([string]$Arguments) {
  $args = @(Split-PythonTopLevel $Arguments)
  if ($args.Count -lt 2 -or -not (Test-PythonLiteral $args[0])) { throw 'non-literal pytest parameter names' }
  $names = Get-PythonLiteralLabel $args[0]
  $list = Remove-PythonGroupingParentheses $args[1]
  if ($list.Length -lt 2 -or -not (($list[0] -eq '[' -and $list[$list.Length - 1] -eq ']') -or ($list[0] -eq '(' -and $list[$list.Length - 1] -eq ')'))) { throw 'non-literal pytest parameter list' }
  $rows = @(@(Split-PythonTopLevel $list.Substring(1, $list.Length - 2)) | Where-Object { $_ })
  $ids = $null
  foreach ($arg in @($args | Select-Object -Skip 2)) {
    if ($arg -match '^\s*ids\s*=\s*(.*)$') {
      $idList = Remove-PythonGroupingParentheses $matches[1]
      if ($idList.Length -lt 2 -or -not (($idList[0] -eq '[' -and $idList[$idList.Length - 1] -eq ']') -or ($idList[0] -eq '(' -and $idList[$idList.Length - 1] -eq ')'))) { throw 'non-literal pytest ids list' }
      $ids = @(@(Split-PythonTopLevel $idList.Substring(1, $idList.Length - 2)) | Where-Object { $_ })
      if ($ids.Count -ne $rows.Count) { throw 'pytest ids list length does not match parameter rows' }
      foreach ($id in $ids) { if (-not (Test-PythonLiteral $id)) { throw 'non-literal pytest id' } }
    }
  }
  $cases = New-Object 'System.Collections.Generic.List[string]'
  for ($i = 0; $i -lt $rows.Count; $i++) {
    $row = $rows[$i].Trim(); $caseId = ''
    if ($row -match '(?s)^pytest\.param\s*\((.*)\)$') {
      $paramArgs = @(Split-PythonTopLevel $matches[1]); $values = New-Object 'System.Collections.Generic.List[string]'
      foreach ($paramArg in $paramArgs) {
        if ($paramArg -match '^\s*id\s*=\s*(.*)$') {
          if (-not (Test-PythonLiteral $matches[1])) { throw 'non-literal pytest.param id' }
          $caseId = Get-PythonLiteralLabel $matches[1]
        } elseif ($paramArg -match '^\s*(?:marks)\s*=') {
          continue
        } elseif ($paramArg -match '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=') {
          throw 'unsupported pytest.param keyword'
        } elseif (-not (Test-PythonLiteral $paramArg)) { throw 'non-literal pytest parameter row' }
        else { $values.Add((ConvertTo-PythonCanonical $paramArg)) }
      }
      $fingerprint = $values -join ','
    } else {
      if (-not (Test-PythonLiteral $row)) { throw 'non-literal pytest parameter row' }
      $fingerprint = ConvertTo-PythonCanonical $row
    }
    if (-not $caseId -and $null -ne $ids) { $caseId = Get-PythonLiteralLabel $ids[$i] }
    $cases.Add($(if ($caseId) { "id=$caseId" } else { "value=$fingerprint" }))
  }
  return [pscustomobject]@{ Names=(ConvertTo-PythonCanonical $names); Cases=@($cases) }
}

function ConvertTo-CaseFingerprint($Value) {
  if ($null -eq $Value) { return 'null' }
  if ($Value -is [string]) { return $Value.Replace('\','\\').Replace(';','\;').Replace('=','\=') }
  if ($Value -is [System.Collections.IDictionary]) {
    $pairs = New-Object 'System.Collections.Generic.List[string]'
    foreach ($key in @($Value.Keys | ForEach-Object { "$_" } | Sort-Object)) { $pairs.Add("$key=$(ConvertTo-CaseFingerprint $Value[$key])") }
    return '{' + ($pairs -join ';') + '}'
  }
  if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
    return '[' + ((@($Value) | ForEach-Object { ConvertTo-CaseFingerprint $_ }) -join ',') + ']'
  }
  if ($Value -is [bool]) { return "$Value".ToLowerInvariant() }
  return [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
}

function Test-PesterLiteralValue($Value) {
  if ($null -eq $Value) { return $true }
  if ($Value -is [scriptblock]) { return $false }
  if ($Value -is [System.Collections.IDictionary]) {
    foreach ($key in $Value.Keys) {
      if (-not (Test-PesterLiteralValue $key) -or -not (Test-PesterLiteralValue $Value[$key])) { return $false }
    }
    return $true
  }
  if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
    foreach ($item in $Value) { if (-not (Test-PesterLiteralValue $item)) { return $false } }
    return $true
  }
  $type = $Value.GetType()
  return ($type.IsPrimitive -or $Value -is [string] -or $Value -is [decimal] -or $Value -is [datetime] -or $Value -is [guid])
}

function Remove-PythonTripleQuotedContent([string]$Content) {
  $characters = $Content.ToCharArray()
  $quote = [char]0
  $ordinaryString = $false
  $tripleString = $false
  $tripleIsFString = $false
  $fExpressionDepth = 0
  $fExpressionQuote = [char]0
  $fExpressionTriple = $false
  $fExpressionEscaped = $false
  $fExpressionComment = $false
  $escaped = $false
  $inComment = $false
  for ($i = 0; $i -lt $Content.Length; $i++) {
    $ch = $Content[$i]
    if ($inComment) {
      if ($ch -eq "`n") { $inComment = $false }
      continue
    }
    if ($ordinaryString) {
      if ($escaped) { $escaped = $false; continue }
      if ($ch -eq '\') { $escaped = $true; continue }
      if ($ch -eq $quote) { $ordinaryString = $false; $quote = [char]0 }
      continue
    }
    if ($tripleString) {
      if ($ch -ne "`r" -and $ch -ne "`n") { $characters[$i] = ' ' }
      if ($tripleIsFString -and $fExpressionDepth -gt 0) {
        if ($fExpressionComment) {
          if ($ch -eq "`n") { $fExpressionComment = $false }
          continue
        }
        if ($fExpressionQuote -ne [char]0) {
          if ($fExpressionEscaped) { $fExpressionEscaped = $false; continue }
          if ($ch -eq '\') { $fExpressionEscaped = $true; continue }
          if ($fExpressionTriple) {
            if ($ch -eq $fExpressionQuote -and $i + 2 -lt $Content.Length -and
                $Content[$i + 1] -eq $fExpressionQuote -and $Content[$i + 2] -eq $fExpressionQuote) {
              $characters[$i + 1] = ' '; $characters[$i + 2] = ' '
              $i += 2; $fExpressionQuote = [char]0; $fExpressionTriple = $false
            }
          } elseif ($ch -eq $fExpressionQuote) { $fExpressionQuote = [char]0 }
          continue
        }
        if ($ch -eq '#') { $fExpressionComment = $true; continue }
        if ($ch -eq "'" -or $ch -eq '"') {
          $fExpressionQuote = $ch
          if ($i + 2 -lt $Content.Length -and $Content[$i + 1] -eq $ch -and $Content[$i + 2] -eq $ch) {
            $characters[$i + 1] = ' '; $characters[$i + 2] = ' '
            $i += 2; $fExpressionTriple = $true
          }
          continue
        }
        if ($ch -eq '{') { $fExpressionDepth++; continue }
        if ($ch -eq '}') { $fExpressionDepth--; continue }
        continue
      }
      if ($tripleIsFString -and $ch -eq '{') {
        if ($i + 1 -lt $Content.Length -and $Content[$i + 1] -eq '{') {
          $characters[$i + 1] = ' '; $i++
        } else { $fExpressionDepth = 1 }
        continue
      }
      if ($tripleIsFString -and $ch -eq '}' -and $i + 1 -lt $Content.Length -and $Content[$i + 1] -eq '}') {
        $characters[$i + 1] = ' '; $i++; continue
      }
      if ($ch -ne $quote -or $i + 2 -ge $Content.Length -or
          $Content[$i + 1] -ne $quote -or $Content[$i + 2] -ne $quote) { continue }
      $backslashes = 0
      for ($back = $i - 1; $back -ge 0 -and $Content[$back] -eq '\'; $back--) { $backslashes++ }
      if (($backslashes % 2) -ne 0) { continue }
      $characters[$i + 1] = ' '; $characters[$i + 2] = ' '
      $i += 2; $tripleString = $false; $tripleIsFString = $false; $quote = [char]0
      continue
    }
    if ($ch -eq '#') { $inComment = $true; continue }
    if ($ch -ne "'" -and $ch -ne '"') { continue }
    $quote = $ch
    if ($i + 2 -lt $Content.Length -and $Content[$i + 1] -eq $quote -and $Content[$i + 2] -eq $quote) {
      $characters[$i] = ' '; $characters[$i + 1] = ' '; $characters[$i + 2] = ' '
      $prefixStart = $i
      while ($prefixStart -gt 0 -and [char]::IsLetter($Content[$prefixStart - 1])) { $prefixStart-- }
      $prefix = $Content.Substring($prefixStart, $i - $prefixStart)
      $prefixBoundary = ($prefixStart -eq 0 -or $Content[$prefixStart - 1] -notmatch '[A-Za-z0-9_]')
      $tripleIsFString = ($prefixBoundary -and $prefix -match '^(?i:f|fr|rf)$')
      $fExpressionDepth = 0; $fExpressionQuote = [char]0; $fExpressionTriple = $false
      $fExpressionEscaped = $false; $fExpressionComment = $false
      $i += 2; $tripleString = $true
    } else {
      $ordinaryString = $true; $escaped = $false
    }
  }
  return (-join $characters)
}

function Get-CommittedUnittestIds([string]$At, [string]$Path) {
  # Parse committed declarations, never import or execute the scanned modules.
  # Resolve only local class ancestry; discovery remains the independent cross-check.
  $resolver = @'
import ast
import json
import posixpath
import subprocess
import sys

repo, commit, selected = sys.argv[1:]
git_env = {k: v for k, v in __import__('os').environ.items() if not k.upper().startswith('GIT_')}
paths = set(subprocess.check_output(['git', '-C', repo, 'ls-tree', '-r', '--name-only', commit], env=git_env, text=True).splitlines())
modules = {}
active = set()
unknown = object()
testcase = ('TestCase', frozenset(), True)

def module_path(current, name, level):
    parts = name.split('.') if name else []
    if level:
        base = posixpath.dirname(current)
        for _ in range(level - 1):
            base = posixpath.dirname(base)
        roots = [base]
    else:
        roots = [posixpath.dirname(current), '', '.agentic/lib']
    for base in roots:
        candidate = posixpath.join(base, *parts)
        for suffix in ('.py', '/__init__.py'):
            if candidate + suffix in paths:
                return candidate + suffix
    return None

def declarations(path):
    if path not in modules:
        source = subprocess.check_output(['git', '-C', repo, 'show', commit + ':' + path], env=git_env)
        modules[path] = (ast.parse(source, filename=path), {})
    return modules[path]

def resolve(path, name):
    key = (path, name)
    if key in active:
        return unknown
    tree, cache = declarations(path)
    if name in cache:
        return cache[name]
    active.add(key)
    value = unknown
    try:
        # Use the final module binding, as unittest does. Conditional/dynamic
        # bindings are intentionally unresolved and cannot supply guessed IDs.
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.asname or alias.name.split('.')[0]) == name:
                        if alias.name == 'unittest':
                            value = ('module', 'unittest')
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) != name:
                        continue
                    if node.module == 'unittest' and alias.name == 'TestCase':
                        value = testcase
                    elif node.module == 'unittest' and alias.name in ('skip', 'skipIf', 'skipUnless'):
                        value = ('decorator', alias.name)
                    else:
                        target = module_path(path, node.module, node.level)
                        value = resolve(target, alias.name) if target else unknown
            elif isinstance(node, ast.ClassDef) and node.name == name:
                bases = [expression(path, base) for base in node.bases]
                known = [base for base in bases if isinstance(base, tuple) and len(base) == 3 and base[2]]
                if known:
                    if any(base is unknown for base in bases):
                        raise RuntimeError('unresolved TestCase ancestry: ' + path + ':' + name)
                    methods = set().union(*(base[1] for base in known))
                    for member in node.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if member.name.startswith('test_'):
                                methods.add(member.name)
                        elif isinstance(member, (ast.Assign, ast.AnnAssign)):
                            targets = member.targets if isinstance(member, ast.Assign) else [member.target]
                            if any(isinstance(t, ast.Name) and t.id.startswith('test_') for t in targets):
                                raise RuntimeError('dynamic TestCase method binding: ' + path + ':' + name)
                    # Standard unittest skip decorators preserve the class and
                    # its collected identities, including inherited methods.
                    # Resolve their bindings rather than trusting their spelling.
                    supported = all(
                        isinstance(decorator, ast.Call) and
                        expression(path, decorator.func) in (
                            ('decorator', 'skip'), ('decorator', 'skipIf'), ('decorator', 'skipUnless'))
                        for decorator in node.decorator_list)
                    if not supported or node.keywords:
                        raise RuntimeError('dynamic TestCase declaration: ' + path + ':' + name)
                    value = (name, frozenset(methods), True)
                else:
                    value = unknown
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                value = unknown
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                    value = unknown
        cache[name] = value
        return value
    finally:
        active.remove(key)

def expression(path, node):
    if isinstance(node, ast.Name):
        return resolve(path, node.id)
    if isinstance(node, ast.Attribute):
        parent = expression(path, node.value)
        if parent == ('module', 'unittest'):
            if node.attr == 'TestCase':
                return testcase
            if node.attr in ('skip', 'skipIf', 'skipUnless'):
                return ('decorator', node.attr)
    return unknown

tree, _ = declarations(selected)
names = set()
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        names.add(node.name)
    elif isinstance(node, ast.ImportFrom):
        names.update(a.asname or a.name for a in node.names if a.name != '*')
ids = []
for name in sorted(names):
    value = resolve(selected, name)
    if isinstance(value, tuple) and len(value) == 3 and value[2]:
        ids.extend(value[0] + '::' + method for method in sorted(value[1]))
print(json.dumps(ids))
'@
  $resolved = & $Py -X utf8 -B -c $resolver $Worktree $At $Path
  if ($LASTEXITCODE -ne 0) { throw "cannot resolve committed unittest ancestry for $Path at $At" }
  return @($resolved | ConvertFrom-Json | ForEach-Object { $_ })
}

function Get-TestIdsAt([string]$At, [hashtable]$StrictPaths, [hashtable]$UnittestIdsByPath = $null) {
  $ids = New-Object 'System.Collections.Generic.List[string]'
  $idCounts = @{}
  $addId = {
    param([string]$Id)
    if ($idCounts.ContainsKey($Id)) { $idCounts[$Id]++ } else { $idCounts[$Id] = 1 }
    $qualified = if ($idCounts[$Id] -eq 1) { $Id } else { "$Id::occurrence=$($idCounts[$Id])" }
    $ids.Add($qualified)
  }
  $paths = @(git -C $Worktree ls-tree -r --name-only $At -- 2>$null)
  if ($LASTEXITCODE -ne 0) { throw "cannot enumerate tree $At" }
  foreach ($rawPath in $paths) {
    $path = "$rawPath" -replace '\\','/'
    if (-not (Test-IsTestFile $path)) { continue }
    & $addId "file:$path"
    $content = (git -C $Worktree show "${At}:$path" 2>$null | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "cannot read $path at $At" }

    $strict = $StrictPaths.ContainsKey($path)
    if ($path -match '(?i)\.py$') {
      if ($null -ne $UnittestIdsByPath -and $strict -and -not $UnittestIdsByPath.ContainsKey($path)) {
        $UnittestIdsByPath[$path] = New-Object 'System.Collections.Generic.List[string]'
      }
      $pythonContent = Remove-PythonTripleQuotedContent $content
      $scopeStack = @()
      $functions = New-Object 'System.Collections.Generic.List[object]'
      $declarations = New-Object 'System.Collections.Generic.List[object]'
      $unittestModuleNames = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
      [void]$unittestModuleNames.Add('unittest')
      foreach ($import in [regex]::Matches($pythonContent, '(?m)^[ \t]*import[ \t]+unittest(?:[ \t]+as[ \t]+(?<alias>[A-Za-z_][A-Za-z0-9_]*))?')) {
        if ($import.Groups['alias'].Success) { [void]$unittestModuleNames.Add($import.Groups['alias'].Value) }
      }
      $unittestCaseNames = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
      foreach ($import in [regex]::Matches($pythonContent, '(?m)^[ \t]*from[ \t]+unittest[ \t]+import[ \t]+(?<items>[^#\r\n]+)')) {
        foreach ($item in $import.Groups['items'].Value.Split(',')) {
          $caseImport = [regex]::Match($item, '^\s*TestCase(?:\s+as\s+(?<alias>[A-Za-z_][A-Za-z0-9_]*))?\s*$')
          if ($caseImport.Success) { [void]$unittestCaseNames.Add($(if ($caseImport.Groups['alias'].Success) { $caseImport.Groups['alias'].Value } else { 'TestCase' })) }
        }
      }
      $knownUnittestClasses = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
      $hasClassBase = $false
      foreach ($lineMatch in [regex]::Matches($pythonContent, '(?m)^(.*)$')) {
        $line = $lineMatch.Groups[1].Value
        $indentText = [regex]::Match($line, '^\s*').Value.Replace("`t", '    ')
        $indent = $indentText.Length

        if ($line -match '^\s*(?:if|elif|else|for|while|try|except|finally|with|match|case)\b') {
          $scopeStack = @($scopeStack | Where-Object { $_.Indent -lt $indent })
        }

        $classMatch = [regex]::Match($line, '^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*\[[^\r\n]*\])?\s*(?:\((?<bases>[^\r\n]*)\))?\s*:')
        if ($classMatch.Success) {
          $scopeStack = @($scopeStack | Where-Object { $_.Indent -lt $indent })
          $className = $classMatch.Groups[1].Value
          $scopeNames = @($scopeStack | Where-Object { $_.Kind -eq 'Class' } | ForEach-Object { $_.Name })
          $qualifiedClass = (@($scopeNames) + $className) -join '::'
          $baseText = $classMatch.Groups['bases'].Value
          if ($baseText.Trim()) { $hasClassBase = $true }
          $isUnittestCase = $false
          foreach ($moduleName in $unittestModuleNames) {
            if ($baseText -match ('(?<![A-Za-z0-9_\.])' + [regex]::Escape($moduleName) + '\s*\.\s*TestCase(?![A-Za-z0-9_])')) { $isUnittestCase = $true; break }
          }
          if (-not $isUnittestCase) {
            foreach ($caseName in $unittestCaseNames) {
              if ($baseText -match ('(?<![A-Za-z0-9_\.])' + [regex]::Escape($caseName) + '(?![A-Za-z0-9_])')) { $isUnittestCase = $true; break }
            }
          }
          if (-not $isUnittestCase) {
            foreach ($knownClass in $knownUnittestClasses) {
              $pythonKnownClass = $knownClass.Replace('::','.')
              if ($baseText -match ('(?<![A-Za-z0-9_\.])' + [regex]::Escape($pythonKnownClass) + '(?![A-Za-z0-9_])') -or
                  $baseText -match ('(?<![A-Za-z0-9_\.])' + [regex]::Escape(($pythonKnownClass -split '\.')[-1]) + '(?![A-Za-z0-9_])')) { $isUnittestCase = $true; break }
            }
          }
          $class = [pscustomobject]@{ Kind='Class'; Offset=$lineMatch.Index; Indent=$indent; Name=$className; Qualified=$qualifiedClass; BindsModule=($scopeStack.Count -eq 0); IsUnittestCase=$isUnittestCase }
          $declarations.Add($class)
          $scopeStack += $class
          if ($isUnittestCase) { [void]$knownUnittestClasses.Add($qualifiedClass) }
          if ($isUnittestCase -and $null -ne $UnittestIdsByPath -and $strict -and -not $UnittestIdsByPath.ContainsKey($path)) {
            $UnittestIdsByPath[$path] = New-Object 'System.Collections.Generic.List[string]'
          }
          continue
        }

        $defMatch = [regex]::Match($line, '^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*\[[^\r\n]*\])?\s*\(')
        if ($defMatch.Success) {
          $scopeStack = @($scopeStack | Where-Object { $_.Indent -lt $indent })
          $functionName = $defMatch.Groups[1].Value
          $scopeNames = @($scopeStack | Where-Object { $_.Kind -eq 'Class' } | ForEach-Object { $_.Name })
          $qualifiedFunction = (@($scopeNames) + $functionName) -join '::'
          $isTest = $functionName -match '^test_'
          $parent = @($scopeStack | Select-Object -Last 1)
          $directClass = if ($parent.Count -and $parent[0].Kind -eq 'Class') { $parent[0].Qualified } else { '' }
          $function = [pscustomobject]@{ Kind='Function'; Offset=$lineMatch.Index; Indent=$indent; Name=$functionName; Qualified=$qualifiedFunction; IsTest=$isTest; DirectClass=$directClass; BindsModule=($scopeStack.Count -eq 0) }
          $functions.Add($function)
          $declarations.Add($function)
          $scopeStack += $function
          if ($isTest) { & $addId "python:$path::$qualifiedFunction" }
          if ($isTest -and $null -ne $UnittestIdsByPath -and $strict -and $parent.Count -and
              $parent[0].Kind -eq 'Class' -and $parent[0].IsUnittestCase) {
            $UnittestIdsByPath[$path].Add("$($parent[0].Qualified)::$functionName")
          }
        }
      }
      if ($null -ne $UnittestIdsByPath -and $strict -and $UnittestIdsByPath.ContainsKey($path) -and
          $UnittestIdsByPath[$path].Count -eq 0) {
        # A strict file is positively non-unittest only when it has no unittest symbols,
        # inheritance, non-pytest from-imports, or common dynamic class/test attachment forms.
        # Everything else stays in the discovery manifest and therefore fails closed.
        $mentionsUnittest = $pythonContent -match '(?i)(?<![A-Za-z0-9_])(?:unittest|TestCase)(?![A-Za-z0-9_])'
        $hasNonPytestFromImport = $false
        foreach ($fromImport in [regex]::Matches($pythonContent, '(?m)^[ \t]*from[ \t]+(?<module>[A-Za-z_][A-Za-z0-9_\.]*)[ \t]+import\b')) {
          if ($fromImport.Groups['module'].Value -cne 'pytest') { $hasNonPytestFromImport = $true; break }
        }
        $hasDynamicTestShape = $pythonContent -match '(?m)\b(?:setattr|type|types\s*\.\s*new_class)\s*\(' -or
          $pythonContent -match '(?m)\b(?:load_tests|__bases__|__class__|__getattr__)\b' -or
          $pythonContent -match '(?m)^[ \t]*[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*\s*\.\s*test_[A-Za-z0-9_]*\s*=' -or
          $pythonContent -match '(?m)^[ \t]*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+' -or
          $pythonContent -match '(?m)^[ \t]*[A-Z][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*\s*\('
        if (-not $mentionsUnittest -and -not $hasClassBase -and -not $hasNonPytestFromImport -and -not $hasDynamicTestShape) {
          [void]$UnittestIdsByPath.Remove($path)
        }
      }
      if ($null -ne $UnittestIdsByPath -and $strict -and $UnittestIdsByPath.ContainsKey($path)) {
        $resolvedIds = @(Get-CommittedUnittestIds $At $path)
        # Keep the original declaration evidence, including duplicate declarations.
        # Add imported/inherited identities without hiding scanner disagreements.
        foreach ($resolvedGroup in @($resolvedIds | Group-Object)) {
          $existing = @($UnittestIdsByPath[$path] | Where-Object { $_ -ceq $resolvedGroup.Name }).Count
          for ($copy = $existing; $copy -lt $resolvedGroup.Count; $copy++) {
            $UnittestIdsByPath[$path].Add($resolvedGroup.Name)
          }
        }
      }
      $pythonTrivia = '(?:[ \t]|\\[ \t]*\r?\n|#[^\r\n]*(?:\r?\n|$))*'
      $pythonGroupedTrivia = '(?:\s|#[^\r\n]*(?:\r?\n|$))*'
      $pytestModuleNames = New-Object 'System.Collections.Generic.List[string]'
      $pytestModuleNames.Add('pytest')
      $importedPytestModuleNames = New-Object 'System.Collections.Generic.HashSet[string]'
      foreach ($import in [regex]::Matches($pythonContent, '(?m)^[ \t]*import[ \t]+(?<items>[^#\r\n]+?)[ \t]*(?:#[^\r\n]*)?\r?$')) {
        foreach ($item in $import.Groups['items'].Value.Split(',')) {
          $pytestImport = [regex]::Match($item, '^[ \t]*pytest(?:[ \t]+as[ \t]+(?<alias>[A-Za-z_][A-Za-z0-9_]*))?[ \t]*$')
          if (-not $pytestImport.Success) { continue }
          $moduleName = if ($pytestImport.Groups['alias'].Success) { $pytestImport.Groups['alias'].Value } else { 'pytest' }
          if (-not $pytestModuleNames.Contains($moduleName)) { $pytestModuleNames.Add($moduleName) }
          [void]$importedPytestModuleNames.Add($moduleName)
        }
      }
      $pytestMarkNames = New-Object 'System.Collections.Generic.List[string]'
      $importedPytestMarkNames = New-Object 'System.Collections.Generic.HashSet[string]'
      foreach ($import in [regex]::Matches($pythonContent, '(?m)^[ \t]*from[ \t]+pytest[ \t]+import[ \t]+')) {
        try {
          $itemsStart = $import.Index + $import.Length
          if ($itemsStart -ge $pythonContent.Length) { throw 'missing pytest import names' }
          if ($pythonContent[$itemsStart] -eq '(') {
            $group = Get-PythonBalancedCall $pythonContent $itemsStart
            if ($null -eq $group) { throw 'unterminated parenthesized pytest import' }
            $lineEnd = $pythonContent.IndexOf("`n", $group.End + 1)
            if ($lineEnd -lt 0) { $lineEnd = $pythonContent.Length }
            $remainder = $pythonContent.Substring($group.End + 1, $lineEnd - $group.End - 1).TrimEnd("`r")
            if ($remainder -notmatch '^[ \t]*(?:#[^\r\n]*)?$') { throw 'unsupported text after parenthesized pytest import' }
            $itemsText = $group.Inner
          } else {
            $itemsBuilder = New-Object Text.StringBuilder
            $position = $itemsStart
            while ($true) {
              $lineEnd = $pythonContent.IndexOf("`n", $position)
              if ($lineEnd -lt 0) { $lineEnd = $pythonContent.Length }
              $line = $pythonContent.Substring($position, $lineEnd - $position).TrimEnd("`r")
              $trimmedLine = $line.TrimEnd()
              $continued = $trimmedLine.EndsWith('\')
              if ($continued) {
                [void]$itemsBuilder.Append($trimmedLine.Substring(0, $trimmedLine.Length - 1))
                [void]$itemsBuilder.Append("`n")
                if ($lineEnd -ge $pythonContent.Length) { throw 'unterminated backslash-continued pytest import' }
                $position = $lineEnd + 1
                continue
              }
              [void]$itemsBuilder.Append($line)
              break
            }
            $itemsText = $itemsBuilder.ToString()
          }

          foreach ($item in @(Split-PythonTopLevel $itemsText)) {
            $importedName = $item.Trim()
            if (-not $importedName) { continue }
            $namedImport = [regex]::Match($importedName, '^(?<name>[A-Za-z_][A-Za-z0-9_]*)(?:\s+as\s+(?<alias>[A-Za-z_][A-Za-z0-9_]*))?$')
            if (-not $namedImport.Success) { throw "unsupported pytest import item '$importedName'" }
            if ($namedImport.Groups['name'].Value -cne 'mark') { continue }
            $markName = if ($namedImport.Groups['alias'].Success) { $namedImport.Groups['alias'].Value } else { 'mark' }
            if (-not $pytestMarkNames.Contains($markName)) { $pytestMarkNames.Add($markName) }
            [void]$importedPytestMarkNames.Add($markName)
          }
        } catch {
          if ($strict) { throw "unresolvable pytest import in $path`: $($_.Exception.Message)" }
        }
      }
      $definitionShadowedPytestModules = New-Object 'System.Collections.Generic.HashSet[string]'
      $definitionShadowedPytestMarks = New-Object 'System.Collections.Generic.HashSet[string]'
      foreach ($declaration in $declarations) {
        if (-not $declaration.BindsModule) { continue }
        if ($importedPytestModuleNames.Contains($declaration.Name)) { [void]$definitionShadowedPytestModules.Add($declaration.Name) }
        if ($importedPytestMarkNames.Contains($declaration.Name)) { [void]$definitionShadowedPytestMarks.Add($declaration.Name) }
      }
      for ($index = $pytestModuleNames.Count - 1; $index -ge 0; $index--) {
        if ($definitionShadowedPytestModules.Contains($pytestModuleNames[$index])) { $pytestModuleNames.RemoveAt($index) }
      }
      for ($index = $pytestMarkNames.Count - 1; $index -ge 0; $index--) {
        if ($definitionShadowedPytestMarks.Contains($pytestMarkNames[$index])) { $pytestMarkNames.RemoveAt($index) }
      }
      $directReferences = New-Object 'System.Collections.Generic.List[string]'
      $groupedReferences = New-Object 'System.Collections.Generic.List[string]'
      if ($pytestModuleNames.Count) {
        $pytestModulePattern = '(?:' + ((@($pytestModuleNames) | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')'
        $directReferences.Add($pytestModulePattern + $pythonTrivia + '\.' + $pythonTrivia + 'mark' + $pythonTrivia + '\.' + $pythonTrivia + 'parametrize' + $pythonTrivia)
        $groupedReferences.Add($pytestModulePattern + $pythonGroupedTrivia + '\.' + $pythonGroupedTrivia + 'mark' + $pythonGroupedTrivia + '\.' + $pythonGroupedTrivia + 'parametrize' + $pythonGroupedTrivia)
      }
      if ($pytestMarkNames.Count) {
        $pytestMarkPattern = '(?:' + ((@($pytestMarkNames) | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')'
        $directReferences.Add($pytestMarkPattern + $pythonTrivia + '\.' + $pythonTrivia + 'parametrize' + $pythonTrivia)
        $groupedReferences.Add($pytestMarkPattern + $pythonGroupedTrivia + '\.' + $pythonGroupedTrivia + 'parametrize' + $pythonGroupedTrivia)
      }
      $parametrizeDecorators = @()
      if ($directReferences.Count) {
        $directParametrize = '^[ \t]*@[ \t]*(?:' + (@($directReferences) -join '|') + ')(?<call>\()'
        $groupedParametrize = '^[ \t]*@[ \t]*\(' + $pythonGroupedTrivia + '(?:' + (@($groupedReferences) -join '|') + ')' + '\)' + $pythonTrivia + '(?<call>\()'
        $parametrizePattern = '(?m)(?:' + $directParametrize + '|' + $groupedParametrize + ')'
        $parametrizeDecorators = @([regex]::Matches($pythonContent, $parametrizePattern))
      }
      foreach ($decorator in $parametrizeDecorators) {
        try {
          $open = $decorator.Groups['call'].Index
          $call = Get-PythonBalancedCall $pythonContent $open
          if ($null -eq $call) { throw 'unparseable pytest parametrize decorator' }
          $target = @($declarations | Where-Object { $_.Offset -gt $call.End } | Select-Object -First 1)
          if (-not $target.Count) { throw 'unparseable pytest parametrize decorator without a following function or class' }
          $parameterSet = Get-PytestCases $call.Inner
          if ($target[0].Kind -eq 'Function') {
            if (-not $target[0].IsTest) { continue }
            foreach ($case in $parameterSet.Cases) { & $addId "pytest-case:$path::$($target[0].Qualified)::params=$($parameterSet.Names)::$case" }
            continue
          }

          $classTests = @($functions | Where-Object { $_.IsTest -and $_.DirectClass -ceq $target[0].Qualified })
          if (-not $classTests.Count) { throw "unresolvable class-level pytest parametrize in $path class $($target[0].Qualified): no directly declared test methods" }
          foreach ($classTest in $classTests) {
            foreach ($case in $parameterSet.Cases) { & $addId "pytest-case:$path::$($classTest.Qualified)::params=$($parameterSet.Names)::$case" }
          }
        } catch {
          if ($strict) {
            $detail = $_.Exception.Message
            if ($detail -notmatch '(?i)unparseable|unresolvable|non-literal|length does not match|unsupported') { $detail = "unparseable pytest parametrize: $detail" }
            throw "$detail in $path"
          }
        }
      }
      if ($strict -and ($definitionShadowedPytestModules.Count -or $definitionShadowedPytestMarks.Count)) {
        foreach ($decorator in [regex]::Matches($pythonContent, '(?m)^(?<indent>[ \t]*)@[ \t]*(?<expression>[^\r\n]*)')) {
          $target = @($declarations | Where-Object { $_.Offset -gt $decorator.Index } | Select-Object -First 1)
          if (-not $target.Count -or $target[0].Kind -ne 'Class') { continue }
          $decoratorIndent = $decorator.Groups['indent'].Value.Replace("`t", '    ').Length
          if ($decoratorIndent -ne $target[0].Indent) { continue }
          $classPrefix = "$($target[0].Qualified)::"
          $containedTests = @($functions | Where-Object { $_.IsTest -and $_.Qualified.StartsWith($classPrefix, [StringComparison]::Ordinal) })
          if (-not $containedTests.Count) { continue }

          $expression = $decorator.Groups['expression'].Value.Trim()
          $expressionEnd = $decorator.Groups['expression'].Index + $decorator.Groups['expression'].Length
          while ($expression.TrimEnd().EndsWith('\') -and $expressionEnd -lt $pythonContent.Length) {
            $nextLineStart = $expressionEnd
            if ($pythonContent[$nextLineStart] -eq "`r") { $nextLineStart++ }
            if ($nextLineStart -lt $pythonContent.Length -and $pythonContent[$nextLineStart] -eq "`n") { $nextLineStart++ }
            $nextLineEnd = $pythonContent.IndexOf("`n", $nextLineStart)
            if ($nextLineEnd -lt 0) { $nextLineEnd = $pythonContent.Length }
            $nextLine = $pythonContent.Substring($nextLineStart, $nextLineEnd - $nextLineStart).TrimEnd("`r")
            $expression += "`n" + $nextLine
            $expressionEnd = $nextLineEnd
          }
          if ($expression.StartsWith('(')) {
            $open = $decorator.Groups['expression'].Index + $decorator.Groups['expression'].Value.IndexOf('(')
            $group = Get-PythonBalancedCall $pythonContent $open
            $capturedEnd = $decorator.Groups['expression'].Index + $decorator.Groups['expression'].Length - 1
            if ($null -ne $group -and $group.End -gt $capturedEnd) { $expression = $pythonContent.Substring($open, $group.End - $open + 1).Trim() }
          }
          $shadowLookupExpression = Remove-PythonGroupingParentheses ([regex]::Replace($expression, '\\[ \t]*\r?\n', ''))
          $shadowed = $false
          foreach ($moduleName in $definitionShadowedPytestModules) {
            $escapedModuleName = [regex]::Escape($moduleName)
            $moduleReference = '(?:' + $escapedModuleName + '|\(' + $pythonGroupedTrivia + $escapedModuleName + $pythonGroupedTrivia + '\))'
            if ($shadowLookupExpression -match ('^' + $moduleReference + $pythonGroupedTrivia + '\.' + $pythonGroupedTrivia + 'mark' + $pythonGroupedTrivia + '\.' + $pythonGroupedTrivia + '[A-Za-z_][A-Za-z0-9_]*\b')) { $shadowed = $true; break }
          }
          if (-not $shadowed) {
            foreach ($markName in $definitionShadowedPytestMarks) {
              $escapedMarkName = [regex]::Escape($markName)
              $markReference = '(?:' + $escapedMarkName + '|\(' + $pythonGroupedTrivia + $escapedMarkName + $pythonGroupedTrivia + '\))'
              if ($shadowLookupExpression -match ('^' + $markReference + $pythonGroupedTrivia + '\.' + $pythonGroupedTrivia + '[A-Za-z_][A-Za-z0-9_]*\b')) { $shadowed = $true; break }
            }
          }
          if ($shadowed) {
            $decoratorIdentity = if ($expression) { '@' + [regex]::Replace($expression, '\s+', ' ') } else { '@<empty>' }
            throw "unresolved class decorator '$decoratorIdentity' in $path class $($target[0].Qualified)"
          }
        }
      }
    }

    if ($path -match '(?i)\.ps1$') {
      $tokens = $null; $parseErrors = $null
      $ast = [System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$tokens, [ref]$parseErrors)
      if ($strict -and @($parseErrors).Count) { throw "unparseable Pester test file $path`: $($parseErrors[0].Message)" }
      $itCommands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and (Get-PesterCommandKind $node) -eq 'It'
      }, $true))
      foreach ($command in $itCommands) {
        $nameResult = Resolve-PesterCommandLabel $command $ast
        $name = if ($nameResult.Resolved) { $nameResult.Value } else { '' }
        $parameterized = @($command.CommandElements | Where-Object {
          $_ -is [System.Management.Automation.Language.CommandParameterAst] -and
          ($_.ParameterName -ieq 'TestCases' -or $_.ParameterName -ieq 'ForEach')
        }).Count -gt 0
        $scopes = New-Object 'System.Collections.Generic.List[string]'
        $ancestor = $command.Parent
        while ($null -ne $ancestor) {
          $scopeType = if ($ancestor -is [System.Management.Automation.Language.CommandAst]) { Get-PesterCommandKind $ancestor } else { '' }
          if ($scopeType -eq 'Describe' -or $scopeType -eq 'Context') {
            $scopeResult = Resolve-PesterCommandLabel $ancestor $ast
            if (-not $scopeResult.Resolved -or -not $scopeResult.Value) {
              if ($strict -and $parameterized) { throw "unresolvable Pester $scopeType scope label in $path" }
            } else {
              $escapedScopeName = $scopeResult.Value.Replace('%','%25').Replace('/','%2F').Replace(':','%3A')
              $scopes.Add("${scopeType}:$escapedScopeName")
            }
          }
          $ancestor = $ancestor.Parent
        }
        $scopeArray = @($scopes)
        [array]::Reverse($scopeArray)
        if ($name) {
          $identity = "pester:$path::$name"
          if ($scopeArray.Count) { $identity += "::scope=$($scopeArray -join '/')" }
          & $addId $identity
        }
        for ($i = 0; $i -lt $command.CommandElements.Count; $i++) {
          $element = $command.CommandElements[$i]
          if ($element -isnot [System.Management.Automation.Language.CommandParameterAst] -or
              ($element.ParameterName -ine 'TestCases' -and $element.ParameterName -ine 'ForEach')) { continue }
          $source = if ($element.ParameterName -ieq 'TestCases') { 'TestCases' } else { 'ForEach' }
          $argument = $element.Argument
          if ($null -eq $argument -and $i + 1 -lt $command.CommandElements.Count) {
            $candidate = $command.CommandElements[$i + 1]
            if ($candidate -isnot [System.Management.Automation.Language.CommandParameterAst] -and
                $candidate -isnot [System.Management.Automation.Language.ScriptBlockExpressionAst]) { $argument = $candidate }
          }
          try {
            if ($null -eq $argument) { throw "missing argument" }
            $rowValue = $argument.SafeGetValue()
            if (-not (Test-PesterLiteralValue $rowValue)) { throw 'unsupported literal value' }
            $rows = @($rowValue)
          } catch {
            if ($strict) {
              $testName = if ($name) { $name } else { '<dynamic>' }
              throw "non-literal Pester -$source rows in $path test '$testName'"
            }
            continue
          }
          if (-not $name) { continue }
          foreach ($row in $rows) {
            $caseIdentity = "pester-case:$path::$name"
            if ($scopeArray.Count) { $caseIdentity += "::scope=$($scopeArray -join '/')" }
            $caseIdentity += "::source=$source::value=$(ConvertTo-CaseFingerprint $row)"
            & $addId $caseIdentity
          }
        }
        if ($strict -and $parameterized -and -not $name) { throw "unparseable Pester It name for parameterized test in $path" }
      }
    }
  }
  return @($ids)
}

function Compare-PythonUnittestCollector([string]$At, [hashtable]$ScannerIdsByPath) {
  if (-not $ScannerIdsByPath.Count) { return @() }
  $collectorRoot = Join-Path ([IO.Path]::GetTempPath()) ('awf116-collector-' + $PID + '-' + [guid]::NewGuid().ToString('N'))
  $treeRoot = Join-Path $collectorRoot 'tree'
  $manifestPath = Join-Path $collectorRoot 'manifest.json'
  $resultPath = Join-Path $collectorRoot 'result.json'
  $scriptPath = Join-Path $collectorRoot 'collect.py'
  $savedGitEnvironment = @{}
  try {
    # Git hooks can export routing/configuration variables into their children.
    # Sanitize preparation and discovery, then restore the caller in finally.
    foreach ($variable in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'GIT_*' })) {
      $savedGitEnvironment[$variable.Name] = $variable.Value
      [Environment]::SetEnvironmentVariable($variable.Name, $null, 'Process')
    }
    $expectedHead = & git -C $Worktree rev-parse --verify "${At}^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "cannot resolve discovery commit $At" }
    $expectedTree = & git -C $Worktree rev-parse --verify "${At}^{tree}" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "cannot resolve discovery tree $At" }
    New-Item -ItemType Directory -Force $collectorRoot | Out-Null
    # --no-local copies objects; no alternates or source worktree metadata.
    & git clone --quiet --no-local --no-checkout -- $Worktree $treeRoot 2>$null
    if ($LASTEXITCODE -ne 0) { throw "cannot clone local source for unittest discovery at $At" }
    & git -C $treeRoot checkout --quiet --detach --force $expectedHead 2>$null
    if ($LASTEXITCODE -ne 0) { throw "cannot check out discovery commit $At" }
    $actualHead = & git -C $treeRoot rev-parse --verify HEAD 2>$null
    if ($LASTEXITCODE -ne 0 -or $actualHead -cne $expectedHead) { throw 'discovery HEAD does not match requested commit' }
    $actualTree = & git -C $treeRoot rev-parse --verify 'HEAD^{tree}' 2>$null
    if ($LASTEXITCODE -ne 0 -or $actualTree -cne $expectedTree) { throw 'discovery tree does not match requested commit' }
    $actualRoot = & git -C $treeRoot rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or [IO.Path]::GetFullPath($actualRoot) -ine [IO.Path]::GetFullPath($treeRoot)) { throw 'discovery root is not the private checkout' }
    $actualGitDir = & git -C $treeRoot rev-parse --absolute-git-dir 2>$null
    if ($LASTEXITCODE -ne 0 -or [IO.Path]::GetFullPath($actualGitDir) -ine [IO.Path]::GetFullPath((Join-Path $treeRoot '.git'))) { throw 'discovery metadata is not private' }

    $manifest = @()
    foreach ($path in @($ScannerIdsByPath.Keys | Sort-Object)) {
      $scannerIds = @()
      foreach ($scannerId in $ScannerIdsByPath[$path]) { $scannerIds += "$scannerId" }
      $manifest += [pscustomobject]@{ path=$path; scanner=$scannerIds }
    }
    [IO.File]::WriteAllText($manifestPath, (ConvertTo-Json -InputObject @($manifest) -Depth 6), (New-Object Text.UTF8Encoding($false)))
    $collectorScript = @'
import hashlib
import importlib.util
import json
import os
import sys
import traceback
import unittest

tree_root, manifest_path, result_path = sys.argv[1:]
os.chdir(tree_root)
sys.path.insert(0, tree_root)

def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item

try:
    with open(manifest_path, encoding="utf-8-sig") as handle:
        manifest = json.load(handle)
    files = []
    for entry in manifest:
        relative_path = entry["path"]
        source_path = os.path.join(tree_root, *relative_path.split("/"))
        module_name = "_awf116_" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
        spec = importlib.util.spec_from_file_location(module_name, source_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot create import spec for " + relative_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)
        if loader.errors:
            raise RuntimeError("; ".join(loader.errors))
        discovery = []
        for test in flatten(suite):
            method_name = getattr(test, "_testMethodName", "")
            if isinstance(test, unittest.TestCase) and method_name.startswith("test_"):
                class_name = test.__class__.__qualname__.replace(".", "::")
                discovery.append(class_name + "::" + method_name)
        scanner = list(entry.get("scanner", []))
        files.append({"path": relative_path, "scanner": sorted(scanner), "discovery": sorted(discovery)})
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump({"schema_version": 1, "status": "ok", "files": files}, handle, sort_keys=True)
except BaseException:
    traceback.print_exc(file=sys.stderr)
    sys.exit(21)
'@
    [IO.File]::WriteAllText($scriptPath, $collectorScript, (New-Object Text.UTF8Encoding($false)))

    $quoted = @($scriptPath,$treeRoot,$manifestPath,$resultPath) | ForEach-Object { '"' + $_.Replace('"','\"') + '"' }
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Py
    $startInfo.Arguments = '-X utf8 -B ' + ($quoted -join ' ')
    $startInfo.WorkingDirectory = $treeRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "could not start Python interpreter '$Py'" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(30000)) {
      try { $process.Kill() } catch {}
      throw 'unittest discovery timed out after 30 seconds'
    }
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    if ($process.ExitCode -ne 0) {
      $detail = (($stderr -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -Last 1)
      if (-not $detail) { $detail = (($stdout -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -Last 1) }
      throw "unittest discovery failed (exit $($process.ExitCode)): $detail"
    }
    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { throw 'unittest discovery produced no result' }
    $result = Get-Content -Raw -LiteralPath $resultPath | ConvertFrom-Json
    if ($null -eq $result -or $result -isnot [System.Management.Automation.PSCustomObject]) {
      throw 'unittest discovery result schema root is not an object'
    }
    $resultProperties = @($result.PSObject.Properties.Name)
    $schemaVersionIsInteger = ($result.schema_version -is [int] -or $result.schema_version -is [long])
    if ($resultProperties -notcontains 'schema_version' -or -not $schemaVersionIsInteger -or $result.schema_version -ne 1 -or
        $resultProperties -notcontains 'status' -or $result.status -isnot [string] -or $result.status -cne 'ok' -or
        $resultProperties -notcontains 'files') {
      throw 'unittest discovery result schema is invalid'
    }
    if ($null -eq $result.files -or $result.files -isnot [System.Array]) {
      throw 'unittest discovery result schema files value is not an array'
    }
    $fileResults = $result.files
    if ($fileResults.Count -ne $manifest.Count) { throw 'unittest discovery result schema has missing or extra file evidence' }
    $resultByPath = @{}
    foreach ($fileResult in $fileResults) {
      if ($null -eq $fileResult -or $fileResult -isnot [System.Management.Automation.PSCustomObject]) {
        throw 'unittest discovery result schema contains non-object file evidence'
      }
      $fileProperties = @($fileResult.PSObject.Properties.Name)
      if ($fileProperties -notcontains 'path' -or $fileProperties -notcontains 'scanner' -or $fileProperties -notcontains 'discovery') {
        throw 'unittest discovery result schema has incomplete file evidence'
      }
      if ($null -eq $fileResult.path -or $fileResult.path -isnot [string] -or -not $fileResult.path) {
        throw 'unittest discovery result schema has a non-string or empty path'
      }
      if ($null -eq $fileResult.scanner -or $fileResult.scanner -isnot [System.Array] -or
          $null -eq $fileResult.discovery -or $fileResult.discovery -isnot [System.Array]) {
        throw "unittest discovery result schema has non-array test evidence for $($fileResult.path)"
      }
      foreach ($testId in $fileResult.scanner) {
        if ($null -eq $testId -or $testId -isnot [string] -or -not $testId) {
          throw "unittest discovery result schema has a non-string or empty scanner test id for $($fileResult.path)"
        }
      }
      foreach ($testId in $fileResult.discovery) {
        if ($null -eq $testId -or $testId -isnot [string] -or -not $testId) {
          throw "unittest discovery result schema has a non-string or empty discovery test id for $($fileResult.path)"
        }
      }
      $resultPathValue = $fileResult.path
      if (-not $resultPathValue -or $resultByPath.ContainsKey($resultPathValue)) { throw 'unittest discovery result schema has an empty or duplicate path' }
      $resultByPath[$resultPathValue] = $fileResult
    }
    $mismatches = @()
    foreach ($entry in $manifest) {
      if (-not $resultByPath.ContainsKey($entry.path)) { throw "unittest discovery result schema has no evidence for $($entry.path)" }
      $fileResult = $resultByPath[$entry.path]
      [string[]]$scannerEvidence = $fileResult.scanner
      [string[]]$discoveryEvidence = $fileResult.discovery
      $expectedScanner = @($entry.scanner | Sort-Object)
      $reportedScanner = @($scannerEvidence | Sort-Object)
      if (($expectedScanner -join "`n") -cne ($reportedScanner -join "`n")) {
        throw "unittest discovery result schema has inconsistent scanner evidence for $($entry.path)"
      }
      $scannerJoined = $reportedScanner -join "`n"
      $discoveryJoined = (@($discoveryEvidence | Sort-Object)) -join "`n"
      if ($scannerJoined -cne $discoveryJoined) {
        $mismatches += [pscustomobject]@{ path=$entry.path; scanner=$reportedScanner; discovery=@($discoveryEvidence | Sort-Object) }
      }
    }
    return @($mismatches)
  } finally {
    foreach ($name in $savedGitEnvironment.Keys) {
      [Environment]::SetEnvironmentVariable($name, $savedGitEnvironment[$name], 'Process')
    }
    if (Test-Path -LiteralPath $collectorRoot) {
      # Git object files may be read-only; normalize only this generated directory.
      $resolvedCollectorRoot = [IO.Path]::GetFullPath($collectorRoot)
      $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
      if (-not $resolvedCollectorRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -or
          (Split-Path $resolvedCollectorRoot -Leaf) -notlike 'awf116-collector-*') { throw 'unsafe collector cleanup path' }
      Get-ChildItem -LiteralPath $collectorRoot -Recurse -Force -File | ForEach-Object { $_.IsReadOnly = $false }
      [IO.Directory]::Delete($resolvedCollectorRoot, $true)
    }
  }
}

function Test-OwnerRuling([string[]]$MissingIds, [string]$Head) {
  $script:AcceptedIntegrityRuling = ''
  $script:IntegrityRulingDiagnostic = ''
  if (-not $RulingsPath -or -not (Test-Path -LiteralPath $RulingsPath -PathType Leaf)) { return $false }
  try { $document = Get-Content -Raw -LiteralPath $RulingsPath | ConvertFrom-Json } catch { $script:IntegrityRulingDiagnostic = "PRE-PUSH GATE: ignoring malformed rulings file $RulingsPath"; return $false }
  $entries = if ($document.PSObject.Properties.Name -contains 'rulings') { @($document.rulings) } else { @($document) }
  foreach ($ruling in $entries) {
    $owner = ''
    $named = @()
    if ("$($ruling.kind)" -ceq 'prepush-test-deletion' -and "$($ruling.decision)" -ceq 'allow') {
      if (-not $Ticket -or "$($ruling.ticket)" -cne $Ticket -or "$($ruling.head)" -cne $Head) { continue }
      if ("$($ruling.owner)" -notmatch '(?i)^Jonathan(?:$|-via-)') { continue }
      $owner = "$($ruling.owner)"
      $named = @($ruling.test_ids | ForEach-Object { "$_" })
    } elseif ($ruling.PSObject.Properties.Name -contains 'id' -and $ruling.PSObject.Properties.Name -contains 'text') {
      $rulingId = "$($ruling.id)"
      $rulingText = "$($ruling.text)"
      if ($rulingId -notmatch '(?i)^Jonathan(?:$|-via-)') { continue }
      if (-not $Ticket -or $rulingText.IndexOf($Ticket, [StringComparison]::Ordinal) -lt 0 -or $rulingText.IndexOf($Head, [StringComparison]::Ordinal) -lt 0) { continue }
      if ($rulingText -notmatch '(?i)\b(?:allow(?:s|ed|ing)?|approv(?:e|es|ed|al)|authoriz(?:e|es|ed|ation)|exempt(?:s|ed|ion)?|waiv(?:e|es|ed|er)|may\s+(?:be\s+)?delet)\b') { continue }
      if ($rulingText -match '(?i)\b(?:do\s+not|must\s+not|not\s+allow(?:ed)?|den(?:y|ies|ied|ial)|refus(?:e|es|ed|al))\b') { continue }
      $owner = $rulingId
      $named = @($MissingIds | Where-Object { $rulingText.IndexOf($_, [StringComparison]::Ordinal) -ge 0 })
    } else { continue }
    if (-not $named.Count) { continue }
    $unnamed = @($MissingIds | Where-Object { $named -cnotcontains $_ })
    if (-not $unnamed.Count) { $script:AcceptedIntegrityRuling = "PRE-PUSH GATE: OWNER-RULING accepted ticket=$Ticket head=$Head owner=$owner test_ids=$($MissingIds -join ', ')"; return $true }
  }
  return $false
}

$head = "$(git -C $Worktree rev-parse HEAD 2>$null)".Trim(); Out "gate start label='$Label' worktree=$Worktree head=$head"
if (-not $head) { Done 3 'not a git worktree' }
if ($Commit) { $want = "$(git -C $Worktree rev-parse $Commit 2>$null)".Trim(); if ($want -ne $head) { Done 3 "worktree HEAD $head is not the commit to push $Commit" } }
$dirty = @(git -C $Worktree status --porcelain --untracked-files=no); if ($dirty.Count) { Done 3 "tracked modifications present: $($dirty -join '; ')" }

$base = "$BaseCommit".Trim()
if (-not $base) {
  $base = "$(git -C $Worktree merge-base origin/main $head 2>$null)".Trim()
  if (-not $base) { $base = "$(git -C $Worktree merge-base main $head 2>$null)".Trim() }
}
$base = "$(git -C $Worktree rev-parse --verify "$base^{commit}" 2>$null)".Trim()
if (-not $base) { Done 3 'cannot resolve the lane merge-base for the pre-push integrity check' }
git -C $Worktree merge-base --is-ancestor $base $head 2>$null
if ($LASTEXITCODE -ne 0) { Done 3 "integrity base $base is not an ancestor of head $head" }
$headTree = "$(git -C $Worktree rev-parse "$head^{tree}" 2>$null)".Trim()
$baseTree = "$(git -C $Worktree rev-parse "$base^{tree}" 2>$null)".Trim()
if (-not $headTree -or -not $baseTree) { Done 3 'cannot resolve head/base trees for the pre-push integrity check' }
if ($headTree -eq $baseTree) {
  $message = "PRE-PUSH GATE: EMPTY-DIFF head $head tree == merge-base $base; nothing pushed"
  Out $message; Record-IntegrityBlock $message $head; Done 6 $message
}

try {
  $strictPaths = @{}
  $touchedPaths = @(git -C $Worktree log --format= --name-only "$base..$head" -- 2>$null)
  if ($LASTEXITCODE -ne 0) { throw "cannot enumerate paths changed in $base..$head" }
  foreach ($touchedPath in $touchedPaths) {
    $normalized = "$touchedPath".Trim() -replace '\\','/'
    if ($normalized -and (Test-IsTestFile $normalized)) { $strictPaths[$normalized] = $true }
  }
  $protected = @{}
  foreach ($id in @(Get-TestIdsAt $base $strictPaths)) { $protected[$id] = $true }
  $commits = @(git -C $Worktree rev-list --reverse "$base..$head" 2>$null)
  if ($LASTEXITCODE -ne 0) { throw "cannot enumerate reachable range $base..$head" }
  foreach ($at in @($commits | Where-Object { $_ -and $_ -ne $head })) {
    foreach ($id in @(Get-TestIdsAt $at $strictPaths)) { $protected[$id] = $true }
  }
  $headUnittestIds = @{}
  $current = @{}; foreach ($id in @(Get-TestIdsAt $head $strictPaths $headUnittestIds)) { $current[$id] = $true }
  $missing = @($protected.Keys | Where-Object { -not $current.ContainsKey($_) } | Sort-Object)
} catch {
  $message = "PRE-PUSH GATE: INTEGRITY-SCAN-ERROR head $head base ${base}: $($_.Exception.Message); nothing pushed"
  Out $message; Record-IntegrityBlock $message $head; Done 6 $message
}
try {
  $collectorMismatches = @(Compare-PythonUnittestCollector $head $headUnittestIds)
} catch {
  $collectorDetail = $_.Exception.Message -replace '[\r\n]+',' '
  $message = "PRE-PUSH GATE: COLLECTOR-ERROR head $head base ${base}: $collectorDetail; nothing pushed"
  Out $message; Record-IntegrityBlock $message $head; Done 7 $message
}
if ($collectorMismatches.Count) {
  $collectorDetails = @($collectorMismatches | ForEach-Object {
    "$($_.path) scanner=[$(@($_.scanner) -join ', ')] discovery=[$(@($_.discovery) -join ', ')]"
  }) -join '; '
  $message = "PRE-PUSH GATE: COLLECTOR-MISMATCH head $head base $base`: $collectorDetails; nothing pushed"
  Out $message; Record-IntegrityBlock $message $head; Done 7 $message
}
if ($missing.Count) {
  $ownerAllowed = [bool](Test-OwnerRuling $missing $head)
  if ($script:IntegrityRulingDiagnostic) { Out $script:IntegrityRulingDiagnostic }
  if (-not $ownerAllowed) {
    $message = "PRE-PUSH GATE: DELETED-TESTS head $head base $base missing test ids: $($missing -join ', '); exact-head owner ruling required; nothing pushed"
    Out $message; Record-IntegrityBlock $message $head; Done 6 $message
  } elseif ($script:AcceptedIntegrityRuling) { Out $script:AcceptedIntegrityRuling }
}

$trailerCode = @"
import subprocess, sys
sys.path.insert(0, r'$Worktree\.agentic\lib')
from agentic.provenance import parse_writer_trailer
bad = []
for sha in sys.argv[1:]:
    raw = subprocess.run(['git', '-C', r'$Worktree', 'cat-file', 'commit', sha], capture_output=True, check=True).stdout
    message = raw.split(b'\n\n', 1)[1].decode('utf-8') if b'\n\n' in raw else ''
    try:
        if parse_writer_trailer(message) is None:
            bad.append(sha + ' missing Writer trailer')
    except Exception as exc:
        bad.append(sha + ' ' + str(exc)[:200])
print('WRITER-TRAILERS ' + ('PASS %d commits' % (len(sys.argv) - 1) if not bad else 'FAIL ' + '; '.join(bad)))
sys.exit(1 if bad else 0)
"@
$oT = & $Py -B -c $trailerCode @($commits | Where-Object { $_ }) 2>&1 | Out-String; $rcT = $LASTEXITCODE; Out ($oT.Trim())   # jk-direct-1427 item 6 (B, stopgap only): AWF-184 Writer-trailer check on base..head; tracked repo fix goes through a normal lane
if ($rcT -ne 0 -or -not [regex]::IsMatch($oT, '(?m)^WRITER-TRAILERS PASS \d+ commits\r?$')) { $message = "PRE-PUSH GATE: WRITER-TRAILERS head $head base ${base}: $((($oT -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()); nothing pushed"; Out $message; Record-IntegrityBlock $message $head; Done 8 $message }
$oA = & powershell -NoProfile -ExecutionPolicy Bypass -File "$W\gate-actionlint.ps1" -Worktree $Worktree 2>&1 | Out-String; $rcA = $LASTEXITCODE; Out ($oA.Trim()); $alSum = "$((($oA -split "`n") | Where-Object { $_ -match '^ACTIONLINT' } | Select-Object -First 1))".Trim()   # DevHead 00:39 (3): pinned actionlint 1.7.12 on any change under .github/workflows/
if ($rcA -ne 0) { Done 4 "actionlint gate failed (exit $rcA): $alSum" }
$env:PYTHONPATH = "$Worktree\.agentic\lib"; $env:PYTHONDONTWRITEBYTECODE = '1'
$code = @"
import sys
sys.path.insert(0, r'$Worktree\.agentic\lib')
from agentic.installer import Tree, verify_release
with Tree(r'$Worktree') as t:
    digest, content = verify_release(t, None, allow_source_checkout=True)
print('RELEASE-CHECK PASS manifest_sha256=%s files=%d' % (digest, len(content)))
"@
$o = & $Py -B -c $code 2>&1 | Out-String; $rc = $LASTEXITCODE; Out ($o.Trim())
if ($rc -ne 0 -or $o -notmatch 'RELEASE-CHECK PASS') { Done 1 "installer release check failed: $((($o -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim())" }
$oI = & $Py -B "$W\import-isolation-check.py" $Worktree 2>&1 | Out-String; $rcI = $LASTEXITCODE; Out ($oI.Trim()); $impSum = "$((($oI -split "`n") | Where-Object { $_ -match '^IMPORT-ISOLATION' } | Select-Object -Last 1))".Trim()   # Jonathan via DevHead 11:09: same standalone-import check as CI (#124 test_module_import_isolation), every lane, blocks push
if ($rcI -ne 0) { Done 5 "standalone-import check failed (exit $rcI): $impSum" }
Push-Location "$Worktree\.agentic\tests"; $o2 = & $Py -B -m unittest test_upgrade_194 2>&1 | Out-String; $rc2 = $LASTEXITCODE; Pop-Location; Out ($o2.Trim())
$ran = [regex]::Match($o2, 'Ran (\d+) test'); $n = if ($ran.Success) { [int]$ran.Groups[1].Value } else { 0 }
if ($n -eq 0) { Done 2 'test_upgrade_194 collected 0 tests (configuration error)' }
if ($rc2 -ne 0 -or $o2 -notmatch '(?m)^OK') { Done 2 "test_upgrade_194 failed: $((($o2 -split "`n") | Where-Object { $_ -match 'FAILED|Error:' } | Select-Object -Last 1))" }
Done 0 "release check PASS; test_upgrade_194 OK ($n tests); $impSum; $alSum"
