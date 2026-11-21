// Data asset holding the closed vocabulary of ability tokens.
#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "Abilities/AbilityTypes.h"
#include "AbilityTokenTable.generated.h"

UCLASS(BlueprintType)
class UAbilityTokenTable : public UPrimaryDataAsset
{
	GENERATED_BODY()

public:
	/** Token name -> definition. The single source of truth for what the rule layer may grant. */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Tokens")
	TMap<FName, FAbilityTokenDefinition> Tokens;

	const FAbilityTokenDefinition* FindToken(FName Token) const;

	/**
	 * The project's token table, loaded on demand and cached.
	 *
	 * This exists because of who needs it: a machine that does not own a character has no loadout manager,
	 * so it has no TokenTable property to read - and those are exactly the machines that need to render
	 * another player's ability effects. Safe to call on the server and on clients.
	 */
	static UAbilityTokenTable* GetDefaultTable();

	/** Fills OutSpec with the token's default parameters. */
	void BuildDefaultSpec(FName Token, FAbilitySpec& OutSpec) const;

	UFUNCTION(BlueprintPure, Category = "Tokens")
	TArray<FName> GetAllTokenNames() const;

	/** Comma separated list of token names, for plain-English error messages. */
	FString GetTokenNameList() const;
};
