#include "Abilities/AbilityTokenTable.h"

UAbilityTokenTable* UAbilityTokenTable::GetDefaultTable()
{
	// Weak, not strong: the table is an ordinary asset and should still be collectable. If it is collected,
	// the pointer goes stale and the next call simply reloads it - LoadObject answers from the already
	// loaded package when it is present, so the steady-state cost is a validity check.
	static TWeakObjectPtr<UAbilityTokenTable> CachedTable;

	if (!CachedTable.IsValid())
	{
		CachedTable = LoadObject<UAbilityTokenTable>(nullptr, TEXT("/Game/Sandbox/DA_AbilityTokens.DA_AbilityTokens"));
		if (!CachedTable.IsValid())
		{
			UE_LOG(LogSandbox, Warning, TEXT("No ability token table found at /Game/Sandbox/DA_AbilityTokens"));
		}
	}

	return CachedTable.Get();
}

const FAbilityTokenDefinition* UAbilityTokenTable::FindToken(FName Token) const
{
	return Tokens.Find(Token);
}

void UAbilityTokenTable::BuildDefaultSpec(FName Token, FAbilitySpec& OutSpec) const
{
	OutSpec.Token = Token;
	OutSpec.Params.Reset();

	if (const FAbilityTokenDefinition* Definition = FindToken(Token))
	{
		OutSpec.Params.Reserve(Definition->DefaultParams.Num());
		for (const TPair<FName, float>& Default : Definition->DefaultParams)
		{
			OutSpec.SetParam(Default.Key, Default.Value);
		}
	}
}

TArray<FName> UAbilityTokenTable::GetAllTokenNames() const
{
	TArray<FName> Names;
	Tokens.GetKeys(Names);
	Names.Sort(FNameLexicalLess());
	return Names;
}

FString UAbilityTokenTable::GetTokenNameList() const
{
	TArray<FString> Names;
	for (const TPair<FName, FAbilityTokenDefinition>& Pair : Tokens)
	{
		Names.Add(Pair.Value.GetDisplayNameString().IsEmpty() ? Pair.Key.ToString() : Pair.Value.GetDisplayNameString());
	}
	Names.Sort();
	return FString::Join(Names, TEXT(", "));
}
