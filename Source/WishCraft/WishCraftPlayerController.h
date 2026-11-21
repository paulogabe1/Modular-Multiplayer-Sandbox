// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerController.h"
#include "WishCraftPlayerController.generated.h"

class UInputMappingContext;
class UUserWidget;
class UAbilityLoadoutManager;
class IHttpRequest;

DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FOnRuleResolution, bool, bApplied, const FString&, Message);

/**
 *  Basic PlayerController class for a third person game
 *  Manages input mappings and owns the replicated ability loadout
 */
UCLASS(abstract)
class AWishCraftPlayerController : public APlayerController
{
	GENERATED_BODY()

public:

	AWishCraftPlayerController();

	/** Replicated loadout that owns the token list and the derived ability components. */
	UFUNCTION(BlueprintPure, Category = "Loadout")
	UAbilityLoadoutManager* GetLoadoutManager() const { return AbilityLoadoutManager; }

	/** Parses plain-text rules, validates them and applies them. Server only. */
	UFUNCTION(BlueprintCallable, BlueprintAuthorityOnly, Category = "Rules")
	bool ApplyRuleText(const FString& RuleText, FString& OutError);

	/** Starts a model request. Return value means accepted, not yet applied. */
	UFUNCTION(BlueprintCallable, BlueprintAuthorityOnly, Category = "Rules")
	bool SubmitRuleText(const FString& RuleText, FString& OutError);

	UPROPERTY(BlueprintAssignable, Category = "Rules")
	FOnRuleResolution OnRuleResolution;

	UPROPERTY(BlueprintReadOnly, Transient, Category = "Rules")
	bool bRuleRequestPending = false;

	/**
	 * This player's preferred camera. Held on the controller, not the pawn, so a respawn does not reset
	 * it. Deliberately NOT replicated: each player picks their own view, and two players may disagree.
	 * Defaults to the shared framing camera.
	 */
	UPROPERTY(BlueprintReadOnly, Category = "Camera")
	bool bPrefersSharedCamera = true;

	/** Console: RulesModel "let me vanish twice as long". Uses the same async path as the panel. */
	UFUNCTION(Exec)
	void RulesModel(const FString& RuleText);

	/** Applies an explicit token list, skipping the parser. Server only. */
	UFUNCTION(BlueprintCallable, BlueprintAuthorityOnly, Category = "Rules")
	bool ApplyRawTokens(const TArray<FName>& Tokens, FString& OutError);

	/** Shows or hides the host rule-entry widget. */
	UFUNCTION(BlueprintCallable, Category = "Rules")
	void ToggleRuleWidget();

	UFUNCTION(BlueprintCallable, Category = "Rules")
	void SetRuleWidgetVisible(bool bVisible);

	/** Console: Rules "fireballs and invisibility only" */
	UFUNCTION(Exec)
	void Rules(const FString& RuleText);

	/** Console: RulesRaw Fireball,Invisibility - bypasses the parser for negative tests. */
	UFUNCTION(Exec)
	void RulesRaw(const FString& CommaSeparatedTokens);

	/** Console: RuleUI - shows or hides the host rule widget. */
	UFUNCTION(Exec)
	void RuleUI();

	/** Re-derives the derived ability components whenever possession changes. */
	virtual void OnPossess(APawn* InPawn) override;
	virtual void AcknowledgePossession(APawn* InPawn) override;

protected:

	/** Created in the constructor so it exists on the server and on every client. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Loadout")
	TObjectPtr<UAbilityLoadoutManager> AbilityLoadoutManager;

	/** Widget blueprint the host uses to type rules. */
	UPROPERTY(EditDefaultsOnly, Category = "Rules")
	TSubclassOf<UUserWidget> RuleWidgetClass;

	UPROPERTY()
	TObjectPtr<UUserWidget> RuleWidget;

	/** Input Mapping Contexts */
	UPROPERTY(EditAnywhere, Category ="Input|Input Mappings")
	TArray<UInputMappingContext*> DefaultMappingContexts;

	/** Input Mapping Contexts */
	UPROPERTY(EditAnywhere, Category="Input|Input Mappings")
	TArray<UInputMappingContext*> MobileExcludedMappingContexts;

	/** Mobile controls widget to spawn */
	UPROPERTY(EditAnywhere, Category="Input|Touch Controls")
	TSubclassOf<UUserWidget> MobileControlsWidgetClass;

	/** Pointer to the mobile controls widget */
	UPROPERTY()
	TObjectPtr<UUserWidget> MobileControlsWidget;

	/** If true, the player will use UMG touch controls even if not playing on mobile platforms */
	UPROPERTY(EditAnywhere, Config, Category = "Input|Touch Controls")
	bool bForceTouchControls = false;

	/** Gameplay initialization */
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

	/** Input mapping context setup */
	virtual void SetupInputComponent() override;

	/** Returns true if the player should use UMG touch controls */
	bool ShouldUseTouchControls() const;


private:
	void FinishRuleRequest(bool bApplied, const FString& Message);
	void ApplyKeywordFallback(const FString& RuleText, const FString& Reason);
	TSharedPtr<IHttpRequest, ESPMode::ThreadSafe> PendingIntentRequest;
	UPROPERTY(EditDefaultsOnly, Category = "Rules")
	FString IntentServiceURL = TEXT("http://127.0.0.1:8077/rules");
};
