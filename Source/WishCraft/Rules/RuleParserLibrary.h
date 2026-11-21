// Deterministic stand-in for the LLM rule layer. Swap ParseRuleText's body for the model call later.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "RuleParserLibrary.generated.h"

class UAbilityTokenTable;

UCLASS()
class URuleParserLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Maps plain text onto the table's closed vocabulary. Fills OutError when nothing matches. */
	UFUNCTION(BlueprintCallable, Category = "Rules")
	static bool ParseRuleText(const FString& RuleText, const UAbilityTokenTable* Table, TArray<FName>& OutTokens, FString& OutError);

	UFUNCTION(BlueprintCallable, Category = "Rules")
	static TArray<FName> ParseTokenList(const FString& CommaSeparatedTokens);

	/** Narrows arbitrary text to a token by name first, then by keyword. */
	static FName FindTokenInText(const FString& Haystack, const FName& Token, const TArray<FString>& Keywords);
};
