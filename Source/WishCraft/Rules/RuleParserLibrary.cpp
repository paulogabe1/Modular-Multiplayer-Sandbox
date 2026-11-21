#include "Rules/RuleParserLibrary.h"

#include "Abilities/AbilityTokenTable.h"

namespace
{
	FString Normalize(const FString& In)
	{
		FString Out = In.ToLower();
		Out.ReplaceInline(TEXT(" "), TEXT(""));
		Out.ReplaceInline(TEXT("-"), TEXT(""));
		Out.ReplaceInline(TEXT("_"), TEXT(""));
		return Out;
	}
}

FName URuleParserLibrary::FindTokenInText(const FString& Haystack, const FName& Token, const TArray<FString>& Keywords)
{
	const FString NormalizedHaystack = Normalize(Haystack);

	if (NormalizedHaystack.Contains(Normalize(Token.ToString())))
	{
		return Token;
	}

	for (const FString& Keyword : Keywords)
	{
		const FString NormalizedKeyword = Normalize(Keyword);
		if (!NormalizedKeyword.IsEmpty() && NormalizedHaystack.Contains(NormalizedKeyword))
		{
			return Token;
		}
	}

	return NAME_None;
}

bool URuleParserLibrary::ParseRuleText(const FString& RuleText, const UAbilityTokenTable* Table, TArray<FName>& OutTokens, FString& OutError)
{
	OutTokens.Reset();
	OutError.Empty();

	if (!Table)
	{
		OutError = TEXT("No ability list is loaded, so the rules cannot be read.");
		return false;
	}

	if (RuleText.TrimStartAndEnd().IsEmpty())
	{
		OutError = TEXT("Type a rule first, for example: fireballs and invisibility only.");
		return false;
	}

	for (const TPair<FName, FAbilityTokenDefinition>& Pair : Table->Tokens)
	{
		if (!FindTokenInText(RuleText, Pair.Key, Pair.Value.Keywords).IsNone())
		{
			OutTokens.AddUnique(Pair.Key);
		}
	}

	OutTokens.Sort(FNameLexicalLess());

	if (OutTokens.Num() == 0)
	{
		OutError = FString::Printf(TEXT("I could not find an ability I know in \"%s\". Try mentioning: %s."),
			*RuleText, *Table->GetTokenNameList());
		UE_LOG(LogSandbox, Warning, TEXT("Rule text matched no tokens: %s"), *RuleText);
		return false;
	}

	UE_LOG(LogSandbox, Log, TEXT("Rule text \"%s\" -> %s"), *RuleText,
		*FString::JoinBy(OutTokens, TEXT(", "), [](const FName& Token) { return Token.ToString(); }));

	return true;
}

TArray<FName> URuleParserLibrary::ParseTokenList(const FString& CommaSeparatedTokens)
{
	TArray<FString> Parts;
	CommaSeparatedTokens.ParseIntoArray(Parts, TEXT(","), true);

	TArray<FName> Tokens;
	for (const FString& Part : Parts)
	{
		const FString Trimmed = Part.TrimStartAndEnd();
		if (!Trimmed.IsEmpty())
		{
			Tokens.Add(FName(*Trimmed));
		}
	}
	return Tokens;
}
