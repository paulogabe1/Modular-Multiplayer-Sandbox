// Owns this player's local mirror of the match ruleset and derives the pawn's ability components from it.
#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Abilities/AbilityTypes.h"
#include "AbilityLoadoutManager.generated.h"

class UAbilityTokenTable;
class UAbilityComponentBase;
class APawn;
struct FLoadoutValidationResult;

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnLoadoutMessage, const FString&, Message);

/**
 * Lives on the PlayerController as a constructor default subobject so it exists on every machine.
 * Loadout is a LOCAL mirror only: the single replicated source of truth is
 * AWishCraftGameState::Ruleset, which every machine can see. The ability components are derived
 * locally from that mirror, on the server and on every client.
 */
UCLASS(BlueprintType)
class UAbilityLoadoutManager : public UActorComponent
{
	GENERATED_BODY()

public:
	UAbilityLoadoutManager();

	UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category = "Loadout")
	TObjectPtr<UAbilityTokenTable> TokenTable;

	/**
	 * Local mirror of the match ruleset. Deliberately NOT replicated: a replicated copy per player
	 * meant every controller had to be pushed to individually. Refreshed from
	 * AWishCraftGameState::Ruleset on BeginPlay, on possession, and whenever the server publishes
	 * new rules.
	 */
	UPROPERTY(BlueprintReadOnly, Category = "Loadout")
	TArray<FAbilitySpec> Loadout;

	UPROPERTY(ReplicatedUsing = OnRep_State, BlueprintReadOnly, Category = "Loadout")
	TArray<FAbilityState> States;

	UPROPERTY(BlueprintAssignable, Category = "Loadout")
	FOnLoadoutMessage OnLoadoutMessage;

	const UAbilityTokenTable* GetTokenTable() const { return TokenTable; }

	UFUNCTION(BlueprintPure, Category = "Loadout")
	FString GetLastError() const { return LastError; }

	UFUNCTION(BlueprintPure, Category = "Loadout")
	TArray<FName> GetEquippedTokens() const;

	UFUNCTION(BlueprintPure, Category = "Loadout")
	FName FindTokenByCategory(EAbilityCategory Category) const;

	UFUNCTION(BlueprintPure, Category = "Loadout")
	float GetCooldownRemaining(FName Token) const;

	UFUNCTION(BlueprintPure, Category = "Loadout")
	bool IsTokenActive(FName Token) const;

	UFUNCTION(BlueprintPure, Category = "Loadout")
	UAbilityComponentBase* FindAbilityComponent(FName Token) const;

	/** Server only: validate then apply. Fills OutError with plain English on rejection. */
	UFUNCTION(BlueprintCallable, BlueprintAuthorityOnly, Category = "Loadout")
	bool ServerSetLoadout(const TArray<FName>& Tokens, FString& OutError);

	/** Server-only transport entry: the validator, never the model, produces numeric specs. */
	bool ServerSetIntentJson(const FString& IntentJson, FString& OutError);

	/** Client entry point; routes to the server. */
	UFUNCTION(BlueprintCallable, Category = "Loadout")
	void RequestSetLoadout(const TArray<FName>& Tokens);

	UFUNCTION(BlueprintCallable, Category = "Loadout")
	void RequestActivateCategory(EAbilityCategory Category);

	UFUNCTION(BlueprintCallable, Category = "Loadout")
	void RequestActivateToken(FName Token);

	/** Rebuilds the derived ability components on the current pawn. */
	void RefreshAbilityComponents();

	/** Copies the match ruleset out of the GameState into this mirror and re-derives abilities. */
	void PullRulesetFromGameState();

	/**
	 * Applies an already-validated ruleset to this mirror. Called by the GameState on every player
	 * whenever the rules change, and by PullRulesetFromGameState on spawn.
	 */
	void ApplyRuleset(const TArray<FAbilitySpec>& InSpecs);

	float GetServerTimeSeconds() const;

	APawn* GetOwningPawn() const;

protected:
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

	UFUNCTION()
	void OnRep_State();

	UFUNCTION(Server, Reliable)
	void ServerSetLoadoutRPC(const TArray<FName>& Tokens);

	UFUNCTION(Server, Reliable)
	void ServerActivateRPC(FName Token);

	/** Routes an ability cosmetic through the pawn, which replicates to every client */
	void PlayEffectOnPawn(FName Token, bool bStarting);

	void ClearAbilityComponents();
	void EndActiveAbility(FName Token);
	void NotifyStateChanged();
	bool CommitValidatedRules(const FLoadoutValidationResult& Result, FString& OutError);

	UPROPERTY()
	TMap<FName, TObjectPtr<UAbilityComponentBase>> AbilityComponents;

	TMap<FName, FTimerHandle> EndTimers;

	UPROPERTY()
	FString LastError;
};
