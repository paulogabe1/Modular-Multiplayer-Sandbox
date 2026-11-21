// Validation layer: the only path through which a rule set may become a live loadout.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Abilities/AbilityTypes.h"
#include "LoadoutValidator.generated.h"

class UAbilityTokenTable;

USTRUCT(BlueprintType)
struct FLoadoutValidationResult
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly)
	bool bValid = false;

	UPROPERTY(BlueprintReadOnly)
	TArray<FAbilitySpec> Specs;

	UPROPERTY(BlueprintReadOnly)
	FString Error;
};

UCLASS()
class ULoadoutValidator : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** How many abilities of this category may be equipped at once. Only Defense is capped by the brief. */
	static int32 GetCategoryCap(EAbilityCategory Category);

	/** Turns a list of token names into clamped specs, or into a plain-English error. */
	UFUNCTION(BlueprintCallable, Category = "Loadout")
	static FLoadoutValidationResult ValidateTokens(const UAbilityTokenTable* Table, const TArray<FName>& Tokens);

	/** Untrusted model output: validate names and bucket labels before building clamped specs. */
	static FLoadoutValidationResult ValidateIntentJson(const UAbilityTokenTable* Table, const FString& IntentJson);
};
